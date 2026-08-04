### 降级策略汇总
IntentRouter ：当 LLM 不可用时，退回到基于规则匹配的 IntentRouter，保证服务不完全挂掉
“帮我查工时”调用链路： 
路由层：根据request解析用户权限上下文，流式输出
langgraph层：长短期记忆注入，prompt构建，
            构建 state，驱动图循环，节点不断根据 state 更新 state


# 一次“填工时”的完整调用链可以理解为：

  用户说“帮我填今天 AI平台 8 小时”
          ↓
  聊天接口接收请求
          ↓
  LLM 选择 save_workhour 并提取参数
          ↓
  TaskExecutor 做权限和认证处理
          ↓
  save_workhour_handler 校验、补全、解析参数
          ↓
  dry_run 闸门判断
          ↓
  Spring Boot POST /api/workhour
          ↓
  返回成功或失败

  ### 1. 前端发送聊天请求
  例如：
  帮我填今天 AI平台 8 小时，完成后端开发
  请求同时带上用户身份：
  {
    "user_id": "u123",
    "entity_type": "employee",
    "auth_token": "JWT..."
  }
  chat.py 会把这些信息组装成 PermissionContext，供后面鉴权使用。
  ### 2. LLM 选择工具并提取参数
  Function Calling 节点判断这是“填报工时”，选择：
  save_workhour
  并生成结构化参数：
  {
    "project_id": "AI平台",
    "date": "2026-07-31",
    "duration": 8,
    "description": "完成后端开发"
  }
  如果缺少项目、日期或时长，系统会先追问，而不是直接调用后端。

  ### 3. TaskExecutor 接管执行
  TaskExecutor 根据工具名找到 save_workhour_handler。
  然后做三件事：
  - 检查当前用户是否有填报权限
  - 把 JWT 注入为 auth_token
  - 设置超时、异常处理和重试机制
  最终传给 handler 的参数大致是：
  {
      "project_id": "AI平台",
      "date": "2026-07-31",
      "duration": 8,
      "description": "完成后端开发",
      "auth_token": "JWT...",
      "user_id": "u123"
  }

  ### 4. handler 做基础校验
  save_workhour_handler 首先检查：
  项目是否为空
  日期格式是否正确
  时长是否为合法的 0.5 小时步长
  时长是否在允许范围内
  例如：
  duration = 7.3
  → 失败，因为工时必须按 0.5 小时递增

  ### 5. 确定工时属于谁
  当前用户 ID 通常来自：
  user_id = kwargs.get("user_id") or user_ctx.get("user_id")
  例如：
  user_id = u123
  最终请求会带：
  "memberId": "u123"
  这一步防止工时被错误地记到其他人名下。

  ### 6. 把项目名称解析成项目 ID
  用户说的是：
  AI平台
  但 Spring Boot 需要：
  projectId = 128
  所以调用 resolve_project_id()：
  "AI平台"
     ↓ 查询项目接口
  "AI平台" → "128"
  如果找不到项目，会返回：
  项目解析失败：未找到名为「AI平台」的项目
  不会继续写入。

  ### 7. 补充后端需要的字段
  系统还会补充：
  - workhourType：正常工时或其他工时
  - workType：研发工作、管理工作等
  - workContent：用户描述的工作内容
  - ISO 格式的 workhourDate
  最终 payload 类似：
  {
    "projectId": "128",
    "memberId": "u123",
    "workhourDate": "2026-07-31T00:00:00Z",
    "workhour": 8,
    "workType": "研发工作",
    "workhourType": "正常工时",
    "workContent": "完成后端开发"
  }

  ### 8. dry-run 安全闸门判断
  如果：
  WRITE_DRY_RUN_DEFAULT=true
  handler 会直接返回：
  {
    "success": true,
    "dry_run": true,
    "preview": {
      "payload": {},
      "summary": "预览（未写入）"
    }
  }
  此时只做参数解析和必要的查询，不会执行写入 POST。
  当前本地 .env.local 就是这个配置，因此本地联调默认不会写生产库。

  ### 9. 真正调用 Spring Boot
  只有 dry_run=False 时，才会执行：
  POST /api/workhour
  并带上：
  Authorization: Bearer <JWT>
  Spring Boot 还会进行最终校验，例如：
  - JWT 是否有效
  - 用户是否有权限填报该项目
  - 日期是否合法
  - 当日工时是否超限
  - 项目 ID 是否存在

  所以 FastAPI 的校验不是最后一道防线，Spring Boot 仍然是业务写入的最终权威。
  补充一点：save_workhour.py 中虽然有“查询当天已有工时”的注释和 _get_daily_total() 函数，但当前 handler 实际没有调用它；每日工时上限主要由 Spring Boot 最终校验。

  ### 10. 返回结果并展示给用户
  写入成功：
  {
    "success": true,
    "message": "工时填报成功：2026-07-31 8h（项目 AI平台）",
    "record_id": "..."
  }
  失败时会把 Spring Boot 的错误信息传回来，例如：
  当天正常工时总和不能超过 8 小时

  一句话总结：
  > LLM 只负责把自然语言转换成 save_workhour 参数；TaskExecutor 负责身份、权限和执行控制；handler 负责参数补全和项目解析；只有通过 dry-run 闸门后，才会向 Spring Boot 发起真正的写入请求。

