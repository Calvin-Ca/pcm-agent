# notepad $PROFILE 
# 将下面这一段复制进去
# 然后. $PROFILE
# use kimi
# 或 use claude
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
	$env:LLM_MODE = "kimi"

        Write-Host "[OK] Using Kimi"

    }

    elseif ($mode -eq "kimi2") {

        # 👉 关键：用 Kimi 专用变量
        $env:ANTHROPIC_API_KEY = "sk-kimi-t6LXUidOfepZrGnjbkaLbh4QdHbF01SpWFlyzMKpQ4clwnu3cT6JxgaseJoyJfD6"
        $env:ANTHROPIC_BASE_URL = "https://api.kimi.com/coding/"

        # 👉 强制清掉 Claude 影响
        # Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

        Ensure-ClaudeOnboarding
	$env:LLM_MODE = "kimi"

        Write-Host "[OK] Using Kimi2"

    }

    # ===== MiniMax =====
    elseif ($mode -eq "minimax") {
        $env:ANTHROPIC_AUTH_TOKEN = "sk-cp-LTbwdPMYZBHl8AgV9slE8Ipj639PpVM1Jq-dgm-_iisgkUyTCcOe5bUIs7863HLcsB9CfDa2UibcZrZJ53fHOygEezWICbDTdKLz3dHRsDo4Bc4VaNVt00c"
        $env:ANTHROPIC_BASE_URL = "https://api.minimaxi.com/anthropic"

        $env:ANTHROPIC_MODEL = "MiniMax-M2.7"
        $env:ANTHROPIC_SMALL_FAST_MODEL= "MiniMax-M2.7"
        $env:ANTHROPIC_DEFAULT_SONNET_MODEL = "MiniMax-M2.7"
        $env:ANTHROPIC_DEFAULT_OPUS_MODEL = "MiniMax-M2.7"
        $env:ANTHROPIC_DEFAULT_HAIKU_MODEL = "MiniMax-M2.7"
        $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = 1
        $env:API_TIMEOUT_MS = "3000000"

        Ensure-ClaudeOnboarding
        $env:LLM_MODE = "minimax"

        Write-Host "[OK] Using MiniMax"
    }

    elseif ($mode -eq "claude") {

        # 👉 清掉 Kimi
        Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_SMALL_FAST_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_DEFAULT_SONNET_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_DEFAULT_OPUS_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_DEFAULT_HAIKU_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC -ErrorAction SilentlyContinue
        Remove-Item Env:API_TIMEOUT_MS -ErrorAction SilentlyContinue

        $env:LLM_MODE = "claude-pro"

        Write-Host "[OK] Using Claude Pro"
    }
    elseif ($mode -eq "status") {
        Write-Host "[INFO] Current Mode: $env:LLM_MODE"
    }
}