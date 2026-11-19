/* Beginner entry points and native-only maintenance, with browser fallbacks. */
function nativeSupport(){return window.pywebview?.api}
function desktopSupportPanel(){
 const native=typeof nativeSupport()?.choose_restore_backup==='function';
 return '<section class="panel"><h3 class="panel-title">备份、恢复与帮助</h3>'+
  '<p class="tip">完整备份包含数据库、岗位截图与可恢复的采集快照。升级数据库前也会自动保存完整备份。</p>'+
  '<div class="page-toolbar"><a class="btn btn-primary" href="/api/capture/backup" download>下载完整备份</a>'+
  '<button class="btn" data-restore-backup'+(native?'':' disabled')+'>选择备份并恢复</button>'+
  '<button class="btn" data-backup-folder'+(native?'':' disabled')+'>打开自动备份目录</button>'+

  '<a class="btn" href="/api/support/diagnostics" download>导出脱敏诊断</a></div>'+
  '<p class="tip">'+(native?'恢复前会预览内容，自动保存当前完整备份，然后关闭服务并重启。':'浏览器模式可以下载完整备份；图形化恢复请在 Windows 桌面版中操作。')+
  ' 诊断文件只包含版本、环境和错误日志摘要，不包含岗位、联系人或日志原文。</p>'+
  '<details id="support-advanced" class="mt-3"><summary>连接器与开发者工具</summary><div class="page-toolbar">'+
  '<button type="button" class="btn" data-boss-settings>设置历史 BossHunter 连接器</button>'+
  '<a class="btn" href="/api/docs" target="_blank" rel="noopener noreferrer">查看 API 文档</a></div></details></section>';
}
async function bossSettings(){
 const bridge=nativeSupport();
 if(typeof bridge?.boss_settings!=='function'){
  modal('历史 BossHunter 连接器', '<p>当前为浏览器模式。此入口维护历史 BossHunter 任务，需要原有本机 8686 端口服务。</p>'+
   '<p>首页 BOSS 检索在 Windows 中直接使用 Chrome 或 Edge。只有继续使用历史 BossHunter 任务时，才需要在桌面版配置原来的 WSL 发行版和启动脚本。</p>'+ 
   '<button class="btn btn-primary" data-quick-job>先粘贴一条 JD</button>');return;
 }
 const info=await bridge.boss_settings();
 modal('历史 BossHunter 连接器', '<p class="notice">'+(!info.wsl_available?'尚未安装 WSL。':info.configured?'已保存连接器配置。':'尚未配置 BossHunter。')+
  ' 首页 BOSS 检索直接使用 Chrome 或 Edge，不依赖此组件。此处用于继续管理历史 BossHunter 任务。</p>'+
  '<ol><li>安装 WSL 与 BossHunter，确认其启动脚本可运行。</li><li>填写你实际使用的发行版和脚本路径，保存后启动连接器。</li><li>返回采集收件箱检查连接，在专用浏览器完成登录。</li></ol>'+
  '<form id="boss-settings-form">'+field('distro','WSL 发行版名称 *','填写已安装的发行版名称','text',true)+
  field('script','WSL 启动脚本绝对路径 *','例如 /home/你的用户名/radar/tools/start-boss-chain.sh','text',true)+
  '<p class="tip">不会自动安装组件，也不会自动开始采集。只运行你明确配置的本机脚本。</p>'+actions()+'</form>'+
  '<button type="button" class="btn mt-3" data-boss-start>启动已保存的连接器</button>');
 $('#boss-settings-form [name=distro]').value=info.distro;
 $('#boss-settings-form [name=script]').value=info.script;
}
async function previewRestore(){
 const bridge=nativeSupport();
 if(typeof bridge?.choose_restore_backup!=='function')throw new Error('请使用桌面版恢复备份');
 const preview=await bridge.choose_restore_backup();if(preview.cancelled)return;
 modal('恢复备份：请核对内容', '<p><strong>'+esc(preview.name)+'</strong></p>'+
  '<p>备份包含 '+preview.jobs+' 个岗位、'+preview.companies+' 家企业、'+preview.applications+' 条投递记录。</p>'+
  '<p class="notice">将替换当前工作空间。系统会先关闭服务，并将当前完整备份保存在数据库旁的 backups 目录；恢复失败会回退原数据。请先停止采集任务。</p>'+
  '<form id="restore-confirm-form" data-token="'+esc(preview.token)+'"><label class="field">'+
  '<span><input type="checkbox" name="confirmed" required> 我确认用此备份替换当前数据，并重启应用</span></label>'+actions()+'</form>');
 $('#restore-confirm-form [type=submit]').textContent='保存当前备份并恢复';
}
function quickAddJob(){
 modal('添加岗位：先保存一条完整 JD', '<p class="tip">填写企业、岗位名称并粘贴原文即可。企业会自动建立，其他信息可以稍后补充。</p>'+
  '<form id="quick-job-form"><div class="field-grid">'+field('company','企业名称 *','','text',true)+
  field('title','岗位名称 *','','text',true)+'</div><label class="field">完整 JD *'+
  '<textarea name="jd_text" required rows="8" placeholder="粘贴招聘页面的岗位职责与任职要求"></textarea></label>'+
  '<details><summary>补充岗位方向、城市与来源链接（可选）</summary><div class="field-grid">'+directionField()+field('city','城市')+field('source_url','来源链接','','url')+'</div></details>'+
  actions()+'</form>',true);
 $('#quick-job-form .form-actions')?.insertAdjacentHTML('afterbegin','<button type="button" class="btn btn-ghost form-actions-lead" data-advanced-job>打开详细录入表</button>');
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-restore-backup],[data-backup-folder],[data-boss-settings],[data-boss-start],[data-quick-job],[data-advanced-job]');
 if(!button)return;
 if(!canLeaveModal())return;
 button.disabled=true;
 try{
  if(button.matches('[data-restore-backup]'))await previewRestore();
  else if(button.matches('[data-backup-folder]'))await nativeSupport().open_backup_folder();
  else if(button.matches('[data-boss-settings]'))await bossSettings();
  else if(button.matches('[data-boss-start]'))toast((await nativeSupport().start_boss_connector()).message);
  else if(button.matches('[data-quick-job]'))quickAddJob();
  else if(button.matches('[data-advanced-job]'))addJob();
 }catch(error){toast(error.message||String(error))}finally{button.disabled=false}
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(!['quick-job-form','boss-settings-form','restore-confirm-form'].includes(form.id))return;
 event.preventDefault();if(!beginFormWrite(form))return;
 try{
  const data=formdata(form);
  if(form.id==='quick-job-form'){
   if(!data.jd_text.trim())throw new Error('请粘贴完整 JD 原文');
   const result=await api('/import/jobs',{method:'POST',body:JSON.stringify({rows:[data],dry_run:false})});
   const row=result.results[0];
   await refreshAfterWrite(row.action==='new'?'岗位已保存。下一步：查看条件，标记关注优先级。':row.action==='review'?'疑似重复，已保存到重复审核，原岗位未覆盖。':'此来源已经收录，打开原岗位核对。');
   await closeModal(true);
   if(row.action==='review'){navigate('reviews');return}
   await jobDetail(row.existing_job_id);
  }else if(form.id==='boss-settings-form'){
   await nativeSupport().save_boss_settings(data.distro.trim(),data.script.trim());
   await closeModal(true);toast('配置已保存，可重新打开设置启动连接器');
  }else{
   await nativeSupport().confirm_restore(form.dataset.token);
   toast('正在关闭服务、备份当前数据并恢复，请等待应用重启');
  }
 }catch(error){showFormError(form,error)}finally{endFormWrite(form)}
});
if(typeof window!=='undefined')window.addEventListener?.('pywebviewready',()=>{if(state.page==='sources')render()});

// ---- 让 AI 助手或自己的 agent 使用这些数据 ----
function agentAccessPanel(){
 return '<section class="panel agent-access"><h3 class="panel-title">让 AI 助手或 agent 使用这些数据 <small>不需要特定的模型或服务</small></h3>'+
  '<div class="rowline"><div><strong>手动交给任意 AI 助手</strong><small>在“AI 分析”导出 JSON，点“复制提示词”，一起发给 DeepSeek、Kimi、Claude、ChatGPT 等任意助手；把它返回的 JSON 用顶栏“导入 AI 结果”导回，先预览再保存。</small></div><button class="btn btn-small" data-page="analysis">打开 AI 分析</button></div>'+
  '<div class="rowline"><div><strong>本地接口（给脚本和 agent）</strong><small>地址 /api/agent/v1，只接受本机访问，需要本地访问令牌。令牌保存在数据目录的 agent-token 文件里，不会显示在网页上。</small></div><button class="btn btn-small" data-agent-access>查看接入方式</button></div>'+
  '<div class="rowline"><div><strong>MCP（Claude Code、Codex、Cursor 等）</strong><small>桌面包可直接作为 MCP 服务运行，无需另装 Python 或取得源码。复制本机配置后，可检测握手及接口连接；写入仍须先预览。</small></div><button class="btn btn-small" data-agent-access>复制 MCP 配置</button></div>'+
  '<p class="tip">无论哪种方式，返回的结果都走同一套校验：参考资料必须逐字出自 JD，写入后仍是待你审核的候选。说明见仓库 docs/agent-handoff.md 与 docs/agent-api.md。</p></section>';
}
async function copyText(text,message){
 try{await navigator.clipboard.writeText(text)}
 catch{const area=document.createElement('textarea');area.value=text;area.className='clipboard-buffer';document.body.append(area);area.select();const ok=document.execCommand('copy');area.remove();if(!ok)throw new Error('无法写入剪贴板，请手动选中复制')}
 toast(message);
}
async function showAgentAccess(){
 const info=await api('/agent-access');
 const mcp=JSON.stringify(info.mcp_config,null,2);
 modal('让 agent 接入择木',
  '<p class="tip">令牌只保存在本机数据目录，网页不会显示它。agent 或 MCP 脚本会自己读取下面的文件。</p>'+
  '<dl class="agent-access-list"><dt>接口地址</dt><dd><code>'+esc(info.api_base)+'</code>（桌面版端口每次启动都会变，见下方地址文件）</dd>'+
  '<dt>令牌文件</dt><dd><code>'+esc(info.token_file)+'</code> <button type="button" class="btn btn-small" data-copy-text="'+esc(info.token_file)+'">复制路径</button></dd>'+
  '<dt>地址文件</dt><dd><code>'+esc(info.endpoint_file)+'</code> '+(info.endpoint_running?'<span class="pill verified">应用运行中</span>':'<span class="pill">源码模式默认 http://127.0.0.1:8785</span>')+'</dd></dl>'+
  '<h3 class="detail-label">MCP 配置示例</h3><pre class="agent-access-snippet">'+esc(mcp)+'</pre>'+
  '<p class="tip">这是当前安装位置对应的真实配置。添加到支持 MCP 的客户端后，保持桌面应用运行。客户端配置格式可能不同，请按客户端说明使用这里的 command 和 args。</p>'+
  '<div id="mcp-check-result" role="status"></div><div class="form-actions"><button type="button" class="btn" data-action="close">关闭</button><button type="button" class="btn" data-mcp-check>检测 MCP 连接</button><button type="button" class="btn btn-primary" data-copy-text="'+esc(mcp)+'">复制 MCP 配置</button></div>');
}
if(typeof document!=='undefined')document.addEventListener('click',async event=>{
 const access=event.target.closest('[data-agent-access]');
 if(access){try{await showAgentAccess()}catch(error){toast(error.message)}return}
 const copy=event.target.closest('[data-copy-text]');
 if(copy){try{await copyText(copy.dataset.copyText,'已复制')}catch(error){toast(error.message)}}
});

if(typeof document!=='undefined')document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-mcp-check]');if(!button||button.disabled)return;
 button.disabled=true;button.textContent='正在检测…';
 try{const result=await api('/agent-mcp/check',{method:'POST'});const target=document.getElementById('mcp-check-result');if(target)target.textContent=result.message;}
 catch(error){toast(error.message);}finally{if(button.isConnected){button.disabled=false;button.textContent='检测 MCP 连接';}}
});
