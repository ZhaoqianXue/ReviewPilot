import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SessionNavigationTests(unittest.TestCase):
    def test_out_of_order_navigation_new_review_and_failure_ownership(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createProjectNavigationOwnership} = require('./frontend/app.js');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const requests = [];
const context = vm.createContext({
  D: {project:{id:'initial'}},
  state: {preservedChatMessages:[{text:'old draft'}], chatPending:true, criteriaDraft:{old:true}},
  reviewUI: {reset(){}},
  chatSubmission: 0, activeTaskMonitor:{key:'old-task',generation:0},
  projectNavigation:createProjectNavigationOwnership('initial'),
  paintWorkspace(){}, monitorActiveTask(){},
  fetchProjectState(id){return new Promise((resolve,reject)=>requests.push({id,resolve,reject}));},
  window:{location:{pathname:'/projects/initial'},history:{pushState(_a,_b,url){this.lastUrl=url;}}},
  setData(data){context.D=data;context.projectNavigation.adoptProject(data.project.id);},
});
vm.runInContext(source.slice(source.indexOf('  function startNavigation('),source.indexOf('  function monitorActiveTask(')),context);
(async()=>{
  const first = context.selectProject('a');
  const second = context.selectProject('b');
  assert.equal(context.state.preservedChatMessages.length,0);
  assert.equal(context.state.chatPending,false);
  requests[0].resolve({project:{id:'a'}}); await first;
  assert.equal(context.D.project.id,'initial');
  assert.equal(context.state.navigationPending,'b');
  requests[1].resolve({project:{id:'b'}}); await second;
  assert.equal(context.D.project.id,'b');
  assert.equal(context.state.navigationPending,'');
  const stale = context.selectProject('a');
  context.startNavigation(''); context.state.navigationPending=''; context.setData({project:{id:''}});
  requests[2].resolve({project:{id:'a'}}); await stale;
  assert.equal(context.D.project.id,'');
  const failed=context.selectProject('missing');
  requests[3].reject(new Error('Network offline')); await failed;
  assert.equal(context.D.project.id,'');
  assert.equal(context.state.navigationPending,'');
  assert.match(context.state.actionError,/Network offline/);
  const old=context.projectNavigation.capture('');
  context.startNavigation('');
  assert.equal(context.projectNavigation.owns(old),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_creation_finishing_after_new_review_cannot_replace_current_conversation(self):
        script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createProjectNavigationOwnership}=require('./frontend/app.js');
const source=fs.readFileSync('./frontend/app.js','utf8');
let resolveCreate, paints=0, refreshes=0;
const context=vm.createContext({D:{project:{id:''}},projectNavigation:createProjectNavigationOwnership(''),
  fetch(){return new Promise(resolve=>resolveCreate=resolve);},
  fetchProjectState:async id=>({project:{id}}),setData(){paints++;},sessionUrl(){},refreshHistory:async()=>refreshes++,JSON});
vm.runInContext(source.slice(source.indexOf('  async function createProjectFromPayload('), source.indexOf('  async function sendProjectChat(')),context);
(async()=>{
  const pending=context.createProjectFromPayload({description:'First topic'});
  context.projectNavigation.begin('');
  resolveCreate({ok:true,json:async()=>({id:'created'})});
  await pending;
  assert.equal(paints,0);assert.equal(refreshes,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
