"""Job/company pagination is presentation-only; export selection remains complete."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGINATOR = ROOT / "static" / "listing-pagination.js"
APP = ROOT / "static" / "app.js"
HTML = ROOT / "static" / "index.html"


def test_listing_code_is_loaded_before_app_and_has_responsive_styles():
    html = HTML.read_text(encoding="utf-8")
    assert html.index("listing-pagination.js") < html.index("/static/app.js")
    assert 'listing-pagination.css' in html
    css = (ROOT / "static" / "listing-pagination.css").read_text(encoding="utf-8")
    for part in (".listing-pagination", ".listing-current-page", ".company-grid+",
                 "@media(max-width:850px)"):
        assert part in css


def test_filtered_export_scope_does_not_use_paginated_rows():
    source = APP.read_text(encoding="utf-8")
    assert "function jobsPage(){return jobWorkbenchPage()}" in source
    assert "listing.items.map(c=>" in source
    assert "const rows=kind==='jobs'?selectedJobs():selectedCompanies()" in source
    assert "导出筛选全部 JSON" in source
    assert "resetListingPage('companies')" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for real pagination JS execution")
def test_real_paginator_boundaries_navigation_and_preserved_selection():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const state = {listPagination:{jobs:{page:1,size:20},companies:{page:1,size:12}}};
let calls = 0, scrolled = false;
const context = {state, render:()=>calls++,
 document:{addEventListener:()=>{},querySelector:()=>({set scrollTop(value){scrolled=true;}})}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/listing-pagination.js','utf8'),context);
const jobs = Array.from({length:657},(_,i)=>({id:i+1}));
const companies = Array.from({length:27},(_,i)=>({id:i+1}));
let first = context.listingPage('jobs',jobs);
assert.equal(first.items.length,20);
assert.equal(first.items[0].id,1);
assert.equal(first.pages,33);
assert.equal(first.from,1);
assert.equal(first.to,20);
assert.equal(first.total,657);
assert.match(context.listingPager('jobs',first,'条岗位'),/上一页/);
assert.match(context.listingPager('jobs',first,'条岗位'),/下一页/);
context.setListingPage('jobs',33);
let last=context.listingPage('jobs',jobs);
assert.equal(last.from,641);
assert.equal(last.to,657);
assert.equal(last.items.length,17);
assert.equal(last.items.at(-1).id,657);
assert.equal(jobs.length,657); // NEVER mutate source selection
assert.ok(scrolled);
context.setListingPage('jobs',9999);
assert.equal(context.listingPage('jobs',jobs).page,33);
context.setListingSize('jobs',50);
assert.equal(state.listPagination.jobs.page,1);
assert.equal(context.listingPage('jobs',jobs).pages,14);
context.setListingSize('jobs',13);
assert.equal(state.listPagination.jobs.size,50); // invalid options ignored
context.resetListingPage('jobs');
assert.equal(state.listPagination.jobs.page,1);
const filtered=jobs.filter(x=>x.id%2===0);
const subset=context.listingPage('jobs',filtered);
assert.equal(subset.total,328);
assert.equal(filtered.length,328);
assert.equal(jobs.length,657);
let co=context.listingPage('companies',companies);
assert.equal(co.items.length,12);
assert.equal(co.pages,3);
context.setListingPage('companies',3);
co=context.listingPage('companies',companies);
assert.deepEqual(Array.from(co.items,x=>x.id),[25,26,27]);
context.setListingPage('companies',9);
co=context.listingPage('companies',companies.slice(0,8));
assert.equal(co.page,1);
assert.equal(co.items.length,8);
assert.equal(co.from,1);
assert.equal(co.to,8);
assert.equal(context.listingPage('jobs',[]).total,0);
assert.equal(context.listingPager('jobs',context.listingPage('jobs',[]),'条岗位'),'');
assert.ok(calls >= 3);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True,
                            text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for JS parse check")
def test_both_scripts_parse_as_real_javascript():
    for name in ("listing-pagination.js", "app.js"):
        result = subprocess.run(["node", "--check", str(ROOT / "static" / name)],
                                capture_output=True, text=True, check=False, timeout=10)
        assert result.returncode == 0, result.stderr
