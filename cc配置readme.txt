# notepad $PROFILE 
# 将下面这一段复制进去
# 然后. $PROFILE
# use kimi
# 或 use cluade
# 最后启动cluade  

function use {
    param([string]$mode)

    function Ensure-ClaudeOnboarding {
        $script = @"
const fs = require('fs');
const os = require('os');
const path = require('path');

const filePath = path.join(os.homedir(), '.claude.json');

let content = {};
if (fs.existsSync(filePath)) {
    content = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
}

content.hasCompletedOnboarding = true;

fs.writeFileSync(filePath, JSON.stringify(content, null, 2), 'utf-8');
"@

        $tempFile = "$env:TEMP\claude_onboarding.js"
        Set-Content -Path $tempFile -Value $script -Encoding UTF8
        node $tempFile
        Remove-Item $tempFile -ErrorAction SilentlyContinue
    }

    if ($mode -eq "kimi") {

        # 👉 关键：用 Kimi 专用变量
        $env:ANTHROPIC_API_KEY = "sk-kimi-lfycj0ZtIlv5D6JjgM1mZ5IOQL6oNEfBDIrwAe83fB2hhdEUPejrYTst8E389PCV"
        $env:ANTHROPIC_BASE_URL = "https://api.kimi.com/coding/"

        # 👉 强制清掉 Claude 影响
        # Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

        Ensure-ClaudeOnboarding

        Write-Host "[OK] Using Kimi"

    }
    elseif ($mode -eq "claude") {

        # 👉 清掉 Kimi
        Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue

        $env:LLM_MODE = "claude-pro"

        Write-Host "[OK] Using Claude Pro"
    }
    elseif ($mode -eq "status") {
        Write-Host "[INFO] Current Mode: $env:LLM_MODE"
    }
}