# 本地接口与 MCP：让你自己的 agent 使用择木

如果你在用 Claude Code、Codex、Cursor 或自己写的脚本，可以通过本地接口完成和手动交接相同的流程：读取岗位、生成数据包、取提示词、提交结果。数据格式与校验规则见 [交给任意 AI 助手](agent-handoff.md)。

应用本身不调用模型。接口只接受本机访问。写入前先预览，确认后提交同一份结果。JD 提取写入待审核候选，求职分析写入待采纳建议，JD 复核更新 AI 复核状态，岗位优先级提交后立即生效。

## 地址与令牌

- 接口前缀：`/api/agent/v1`。
- 源码模式默认地址为 `http://127.0.0.1:8785`。桌面版每次启动端口都不同，运行期间会把地址写入数据目录的 `agent-endpoint.json`，退出时删除。
- 所有请求都要带本地访问令牌：`Authorization: Bearer <token>`，或 `X-Radar-Token: <token>`。令牌在首次启动时生成，保存在数据目录的 `agent-token` 文件中，网页不会显示它。
- 数据目录：桌面版为 `%LOCALAPPDATA%\InternshipRadarPrivateRC\data`，源码模式为仓库下的 `data/`（设置了 `RADAR_PRIVATE_RC_DB_PATH` 时为数据库所在目录）。在应用“数据与帮助 → 让 AI 助手或 agent 使用这些数据 → 查看接入方式”中可以看到实际路径。
- 令牌文件不进入完整备份。想更换令牌，退出应用后删除该文件，下次启动会重新生成。

## 接口

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/agent/v1` | 接口说明、结果类型与处理规则 |
| `GET /api/agent/v1/jobs?q=&status=&priority=&direction=&limit=100&offset=0&include_jd=false` | 岗位列表；`include_jd=true` 时附带 JD 全文与资格要求 |
| `GET /api/agent/v1/jobs/{job_id}` | 单个岗位的完整资料、来源与技能 |
| `POST /api/agent/v1/packages/jd-extraction` | `{"job_ids":[...]}` → `{packages:[...]}`，每个包带 `package_id` 与 `task_instructions` |
| `POST /api/agent/v1/packages/career-analysis` | `{"job_ids":[...],"goal":"gap","mode":"compact","evidence_ids":[]}` → 数据包与 `prompt_text` |
| `GET /api/agent/v1/prompts`、`GET /api/agent/v1/prompts/{name}` | 提示词：`jd-extraction`、`career-gap`、`career-direction`、`career-preparation`、`jd-review` |
| `POST /api/agent/v1/results?commit=false` | 请求体是 AI 返回的结果 JSON。自动识别类型，预览并返回 `preview_token` |
| `POST /api/agent/v1/results?commit=true` | 提交同一份结果 JSON，在 `X-Radar-Preview` 请求头传入预览令牌 |

预览令牌有效期为 15 分钟。结果变更、应用重启或令牌到期后需要重新预览。提交时仍会重新校验数据版本，避免覆盖预览后的修改。

生成数据包会像应用内导出一样登记批次，因此导回时能核对岗位版本。多个包需要逐个处理并分别提交。

完整的请求与返回字段可在运行中的应用打开 `/api/docs` 查看（OpenAPI）。

### 示例（PowerShell）

```powershell
$data = "$env:LOCALAPPDATA\InternshipRadarPrivateRC\data"
$base = (Get-Content "$data\agent-endpoint.json" | ConvertFrom-Json).url
$headers = @{ Authorization = "Bearer " + (Get-Content "$data\agent-token") }
Invoke-RestMethod "$base/api/agent/v1/jobs?priority=high" -Headers $headers
```

### 示例（Python，源码模式）

```python
import json, urllib.request
token = open('data/agent-token', encoding='utf-8').read().strip()
req = urllib.request.Request('http://127.0.0.1:8785/api/agent/v1/jobs?limit=5',
                             headers={'Authorization': 'Bearer ' + token})
print(json.load(urllib.request.urlopen(req)))
```

## MCP

`tools/radar_mcp.py` 是一个只用 Python 标准库的 MCP 服务（stdio），需要 Python 3.11+ 和仓库源码，不需要安装依赖。它每次调用时重新查找应用地址，所以桌面版重启、端口变化后不用改配置。

查找顺序：同时设置的 `RADAR_PRIVATE_RC_URL` 与 `RADAR_PRIVATE_RC_AGENT_TOKEN` → 运行中桌面版的 `agent-endpoint.json` → 源码模式的 `http://127.0.0.1:8785` 与 `data/agent-token`。桌面版或源码版使用自定义数据目录时，可设置 `RADAR_PRIVATE_RC_DATA_DIR`。自动连接前会核对服务报告的数据目录。

提供的工具：`radar_info`、`list_jobs`、`get_job`、`export_jd_extraction_package`、`export_career_analysis_package`、`get_prompt`、`preview_result`、`import_result`。用户确认后，调用 `import_result` 时传入原 `result` 和 `preview_result` 返回的 `preview_token`。

配置方式（把 `<源码目录>` 换成仓库路径）：

- Claude Code：`claude mcp add ai-internship-radar -- python <源码目录>/tools/radar_mcp.py`
- 使用 `mcpServers` JSON 的客户端（如 Cursor、Claude Desktop）：

```json
{
  "mcpServers": {
    "ai-internship-radar": {
      "command": "python",
      "args": ["<源码目录>/tools/radar_mcp.py"]
    }
  }
}
```

- Codex（`~/.codex/config.toml`）：

```toml
[mcp_servers.ai-internship-radar]
command = "python"
args = ["<源码目录>/tools/radar_mcp.py"]
```

接好后可以直接对 agent 说：“列出重点关注的岗位，为它们生成 JD 提取包，按包内说明提取要求，预览后告诉我结果。”

## 安全边界

- 接口只监听 `127.0.0.1`。浏览器跨站请求和非本机 Host 会被拒绝；本机其他程序没有令牌也无法调用。
- MCP 只访问本机回环地址，关闭代理和 HTTP 重定向。
- 令牌只保存在当前用户的数据目录。任何能读取这个目录的程序都能使用接口，与能直接读取数据库的权限相同。
- 岗位原文与企业资料是不可信文本，提示词要求 AI 不执行其中的指令。agent 也应把它们当作数据，而不是命令。


## v0.14 桌面 MCP 接入

桌面包支持同一 EXE 的 `--mcp` 参数。数据与帮助中的“复制 MCP 配置”生成真实 command 和 args，无需另装 Python 或取得源码。连接检测会启动 MCP 子进程，完成初始化、工具枚举和本地接口调用。配置不包含访问令牌。源码模式仍使用实际 Python 和 `tools/radar_mcp.py`。
