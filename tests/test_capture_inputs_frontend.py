"""Capture-inbox search inputs follow the home-page rules, and option lists keep their order."""
from fe_helpers import harness_script, run_node


def run(body):
    run_node(harness_script("""
const ctx=fe.boot({console,
 document:{addEventListener(){},querySelector(){return null},querySelectorAll(){return []},
  body:{classList:{add(){},remove(){},toggle(){},contains(){return false}}}},
 window:{matchMedia(){return {matches:false,addEventListener(){}}}},
 localStorage:{getItem(){return null},setItem(){}}});
fe.loadCore(ctx);
fe.publish(ctx,'opt','captureCityValues','normalizeCaptureEntries','captureCityField');
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
""" + body))


def test_capture_keywords_and_cities_accept_the_home_page_separators():
    run("""
same(ctx.captureCityValues('北京，上海、深圳\\n北京; 杭州'),['北京','上海','深圳','杭州']);
assert.equal(ctx.normalizeCaptureEntries('AI 产品 实习\\nAgent 产品实习；RAG'),'AI 产品 实习，Agent 产品实习，RAG');
assert.equal(ctx.normalizeCaptureEntries(' \\n '),'');
""")


def test_capture_city_field_shows_common_chips_custom_cities_and_a_hidden_value():
    run("""
const html=ctx.captureCityField(['北京','喀什地区']);
assert.match(html,/<input type="hidden" name="city" value="北京，喀什地区">/);
assert.match(html,/data-capture-city="北京" aria-pressed="true"/);
assert.match(html,/data-capture-city="上海" aria-pressed="false"/);
assert.match(html,/data-capture-city-remove="喀什地区"/);
assert.match(html,/id="capture-city-input"[^>]*maxlength="60"/);
assert.match(ctx.captureCityField([]),/尚未选择城市/);
""")


def test_option_pairs_keep_the_placeholder_first_even_with_numeric_ids():
    run("""
const html=ctx.opt([['','请选择企业'],['12','星图'],['3','北辰']],'');
assert.ok(html.indexOf('请选择企业')<html.indexOf('星图'));
assert.match(html,/<option value="" selected>请选择企业/);
assert.match(ctx.opt({7:'A',8:'B'},7),/<option value="7" selected>A/);
assert.match(ctx.opt({x:'X'},'x'),/value="x" selected/);
""")
