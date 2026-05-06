#!/bin/bash
# 修复 SSH 反向隧道
killall autossh 2>/dev/null
sleep 2
# 在 116 上杀掉旧的 notty SSH 会话（后台延迟执行，避免中断当前连接）
ssh -o ConnectTimeout=5 useryzk@116.205.174.57 'nohup sh -c "sleep 5; ps aux | grep sshd.*notty | grep -v grep | awk \"{print \\$2}\" | while read pid; do kill -9 \$pid 2>/dev/null; done" >/dev/null 2>&1 &' >/dev/null 2>&1
sleep 8
# 启动新隧道
nohup autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 9901:127.0.0.1:8000 useryzk@116.205.174.57 > /tmp/autossh.log 2>&1 &
sleep 2
echo "autossh status:"
pgrep -a autossh
echo "tunnel test:"
ssh -o ConnectTimeout=5 useryzk@116.205.174.57 'curl -s -o /dev/null -w "%{http_code} %{time_total}s" --max-time 5 http://127.0.0.1:9901/docs'
echo ""
