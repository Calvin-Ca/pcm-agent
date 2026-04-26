# CDN/WAF 403 诊断报告 — 2026-04-23

## TL;DR

> **运维白名单已生效,公网 POST `/api/ai/chat` 对正常用户可用**。
> **测试中看到的 403 是华为云 WAF 对测试客户端 IP 做了频率限流**,不是路径规则问题,也不影响终端用户。
> 之前 E2E 报告里把 E1 标成 P0"阻断业务"诊断错方向,本文档是修正结论。

---

## 一、证据:同一时刻,不同 IP 结果完全相反

当天 20:27 同一 token,POST `https://gst.thsware.com/api/ai/chat`:

| 客户端 | 路径 | HTTP | 耗时 | 说明 |
|--------|------|------|------|------|
| 开发机(本地 Windows) | 公网 DNS → CDN | **403** | 0.04s | CDN 层快速拒绝 |
| 116 服务器(走公网域名,不 --resolve) | 公网 DNS → CDN | **200** | 23.8s | SSE 流正常,`query_timesheet` 真实触发 |
| 116 服务器(`--resolve 127.0.0.1`) | 绕 CDN 直连 nginx | **200** | 23.8s | 全程链路正常 |

开发机 GET `/api/ai/health` 也返回 **503**,而 116 上 GET `/api/ai/health` 同样 503 —— 说明这个 IP 已被 WAF 标记,不是业务路径问题。

---

## 二、关键指纹:403 是 WAF 发的,不是源站

从 403 响应 header 里能一眼识别:

```
HTTP/1.1 403
Content-Length: 0
Set-Cookie: HWWAFSESID=6e79573c9ca401aaab; path=/   ← 华为云 WAF session id
Set-Cookie: HWWAFSESTIME=1776946903912; path=/
Server: CW                                           ← 华为云边缘节点
WWW-Authenticate: Bearer                             ← 这个是 SpringBoot 默认兜底
```

两个关键线索:

1. **Body 为空 + 极短耗时(0.04s)** — WAF 层快速拒绝的典型特征。如果是源站返回的 403,会经过 nginx→SpringBoot 链路,至少 100~300ms。
2. **`HWWAFSESID` / `Server: CW`** — 华为云 WAF 指纹,不是源站 nginx(`Server: nginx/xxx`)也不是 SpringBoot(`Server: Apache-Coyote` 之类)。

---

## 三、为什么会 403?触发的是频率规则,不是路径规则

华为云 WAF 默认开启"**CC 防护 / 精准访问控制**"等规则,对**短时间内同一 IP 的高频请求**会自动拦截,返回 403 + 设置 `HWWAFSESID` cookie 用于后续的人机挑战/退避。

触发场景(当天实测过程命中):

- 下午 3~4 小时内从同一开发机反复打 `/api/ai/chat`(首次 E2E 脚本 9 连发 + 多次单独对比实验)
- curl 默认 UA `curl/x.x.x`,WAF 更倾向标记为自动化流量
- 多次带 `Authorization` header + 大小写变体的对比测试,被识别为"探测行为"

> 另一种可能性是 WAF 对 `POST + application/json` 做了请求体语义检查(`message` 字段里带中文 + JSON 结构),但**GET /health 也 503** 这点基本排除了内容拦截,更像 IP 级封禁。

---

## 四、测试时如何规避

按影响递减排序,推荐前 2 条优先落地:

### 1. **用 116 服务器作为测试入口**(首选)

企业公网 IP 在 WAF 白名单/信誉库里的权重远高于个人 IP。把 `scripts/e2e-regression-0423.sh` 推到 116 执行:

```bash
# 从本机把脚本传到 116
cat scripts/e2e-regression-0423.sh | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/e2e.sh && chmod +x /tmp/e2e.sh'"

# 在 116 上跑
ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/e2e.sh'"
```

脚本里 URL 用 `https://gst.thsware.com`(走真公网 CDN),不要再用 `--resolve 127.0.0.1` 绕 CDN,否则不能代表用户路径。

### 2. **限制请求频率 + 串行化**

脚本里每条 curl 之间加 `sleep 3`,避免被判为 burst。9 个用例 × (请求 25s + sleep 3s) ≈ 4 分钟,不影响效率。

```bash
run T1 "查我本周工时" e2e-t1
sleep 3
run T2 "查一下我参与的项目" e2e-t2
sleep 3
...
```

### 3. **curl 带上浏览器 User-Agent / Origin / Referer**

让请求看起来像真实前端,降低被 WAF 风控打分的概率:

```bash
curl ... \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0" \
  -H "Origin: https://gst.thsware.com" \
  -H "Referer: https://gst.thsware.com/" \
  ...
```

### 4. **保留 HWWAFSESID cookie**

首次 GET 拿到 cookie 后,后续请求带着 cookie 可能被 WAF 当作"同一会话",减少被重新评估的概率:

```bash
curl -c /tmp/wafjar.txt -b /tmp/wafjar.txt ...
```

### 5. **被限流后的应急处理**

- **等 15~30 分钟自动解封**(华为云 WAF 频率规则滑窗默认 10~30 分钟)
- **换 IP**:用 116/172、或换手机 4G 热点临时继续测
- **找运维加 IP 白名单**:开发机固定 IP 可以申请加入 WAF 白名单(需要周期性续期)
- **绕 CDN**:`--resolve gst.thsware.com:443:<116 公网 IP>`,但这条路径不代表用户,只用于快速验证源站是否正常

### 6. **浏览器实测是最终验收标准**

任何脚本测试通过都不等于用户能用。最后必须:

1. 清浏览器缓存 + cookie
2. 登录 https://gst.thsware.com
3. 打开 AI 对话面板,发一条"查我本周工时"
4. DevTools Network 看 `/api/ai/chat` 的请求头、响应状态、SSE 流

浏览器走的是用户真实 UA + Referer + 正常交互节奏,最能反映生产状况。

---

## 五、对 E2E 文档的修正

`docs/archive/tasks-2026-04/e2e-regression-2026-04-23.md` 里 E1 原本标为"P0 阻断业务",结论是错的。**实际状态**:

| 视角 | 状态 |
|------|------|
| 终端用户(浏览器走 CDN) | ✅ 可用(116 实测 POST 200) |
| 自动化脚本(curl 高频) | ⚠️ 可能触发 WAF 频率规则,按第四节规避 |
| 绕 CDN 直连 nginx(--resolve) | ✅ 始终可用(不经 WAF) |

E1 降级为 🟡 注意事项,不再是 P0 阻断。

---

## 六、需要前端/运维进一步确认的事项

- [ ] 前端同事用浏览器实测一次 AI 对话,确认生产可用
- [ ] 运维侧如果后续要加 IP 白名单,把开发同事固定出口 IP 报给华为云控制台
- [ ] 运维侧在华为云 WAF 控制台看一下 CC 防护规则阈值,如果对 `/api/ai/chat` 这种"慢请求接口"(每次 20~30s)触发太敏感,可以单独调高这条路径的阈值
