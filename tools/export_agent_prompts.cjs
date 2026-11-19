/* Export the frontend's AI prompts to static/prompts/*.md so the agent API and MCP can serve
   exactly the same text the app copies or downloads. Run after editing a prompt:
     node tools/export_agent_prompts.cjs          (write)
     node tools/export_agent_prompts.cjs --check  (exit 1 when the files are stale) */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const staticDir = path.join(root, 'static');
const outDir = path.join(staticDir, 'prompts');

function prompts() {
  const ctx = vm.createContext({});
  const career = fs.readFileSync(path.join(staticDir, 'career-prompts.js'), 'utf8');
  vm.runInContext(career + '\nthis.careerPromptFor = careerPromptFor;', ctx);
  const view = fs.readFileSync(path.join(staticDir, 'analysis-view.js'), 'utf8');
  const start = view.indexOf('const requirementPrompt = `');
  const end = view.indexOf('`;', start);
  if (start < 0 || end < 0) throw new Error('requirementPrompt not found in analysis-view.js');
  vm.runInContext(view.slice(start, end + 2) + '\nthis.requirementPrompt = requirementPrompt;', ctx);
  return {
    'jd-extraction': ctx.requirementPrompt,
    'career-gap': ctx.careerPromptFor('gap'),
    'career-direction': ctx.careerPromptFor('direction'),
    'career-preparation': ctx.careerPromptFor('preparation'),
  };
}

const check = process.argv.includes('--check');
const stale = [];
fs.mkdirSync(outDir, {recursive: true});
for (const [name, text] of Object.entries(prompts())) {
  const file = path.join(outDir, name + '.md');
  const body = text.replace(/\r\n/g, '\n');
  const current = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n') : null;
  if (current === body) continue;
  if (check) stale.push(name);
  else fs.writeFileSync(file, body, 'utf8');
}
if (stale.length) {
  console.error('Prompt files are stale: ' + stale.join(', ') + '. Run node tools/export_agent_prompts.cjs');
  process.exit(1);
}
console.log(check ? 'prompt files up to date' : 'prompt files written to static/prompts');
