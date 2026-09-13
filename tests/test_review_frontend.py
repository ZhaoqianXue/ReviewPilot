"""Exercise review UI state with a fake API, without a browser or model calls."""
from pathlib import Path
import subprocess
import unittest


class ReviewFrontendTests(unittest.TestCase):
    def test_text_and_structured_corrections_preserve_types_and_selection_is_idempotent(self):
        script = r'''
const assert = require('node:assert/strict');
global.window = {};
require('./frontend/review.js');
(async () => {
  for (const [type, input, expected] of [['Text','A quoted finding','A quoted finding'], ['List','["a","b"]',['a','b']], ['integer','42',42]]) {
    let payload;
    const data = {project:{id:'test'},reviewWorkbench:{revision:'rev',screening:[],extraction:[]}};
    global.fetch = async (_path, options) => ({ok:true,json:async()=> {
      if (options.method==='PATCH') {payload=JSON.parse(options.body);return {state:data};}
      return {key:'paper',field:'field',type,value:'',revision:'rev',evidence:{quote:'',pages:[],sourceUrls:[]}};
    }});
    const ui=window.ReviewWorkbench.create({getData:()=>data,paint:()=>{},setData:()=>{},busy:()=>false});
    await ui.click('review-field-detail',{dataset:{key:'paper',field:'field'}});
    ui.input({dataset:{reviewInput:'value'},value:input},{});
    ui.input({dataset:{reviewInput:'reason'},value:'Reviewed source'},{});
    await ui.submit({id:'rp-record-review-form'});
    assert.deepEqual(payload.value,expected);
    assert.equal(ui.isOpen(),false);
    const checkbox={dataset:{reviewPick:'paper',mode:'extraction'},checked:true};
    ui.input(checkbox,{});ui.input(checkbox,{});
    assert.match(ui.extraction(),/Run live sample \(1\/5\)/);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
