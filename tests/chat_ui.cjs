// NODE_PATH must contain jsdom and jquery. No Oracle/network requests are made.
const {JSDOM} = require('jsdom');
const jquery = require('jquery');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../oracle_serv/chat.js'), 'utf8');
const cid = '11111111-1111-4111-8111-111111111111';
const fid = '22222222-2222-4222-8222-222222222222';
const file = {file_id: fid, filename: 'debt.txt', size_bytes: 4};
const delay = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(check) {
    for (let i = 0; i < 200; i++) { if (check()) return; await delay(); }
    throw new Error('UI timeout');
}
async function fixture() {
    const dom = new JSDOM(`<main id="chat_body"><div class="head-actions"></div><div class="chathead"></div><div id="chatStatus"></div><div id="chatTitle"></div><div id="chatMessages"></div><div id="chatList"></div><input id="chatSearch"><button id="newChatBtn"><span class="new-chat-plus"></span></button><div class="composer"><div id="selectedFiles"></div><textarea id="prompt"></textarea><button id="attachBtn"></button><button id="sendBtn"></button></div><input type="file" id="P237_GPT_FILES"><div class="hint"></div></main>`, {runScripts:'outside-only', url:'http://example.test'});
    const win = dom.window, $ = jquery(win);
    let active = '', fail = false, stored = false;
    const calls = [];
    win.$v = () => active;
    win.$s = (_name, value) => {active = value;};
    win.apex = {jQuery:$, server:{
        url: args => '/download?id=' + args.x01,
        process: (name, data, options) => {
            calls.push({name, data});
            setTimeout(() => {
                if (name === 'GPTConfig') return options.success({max_files:5,max_file_bytes:5242880,extensions:['.txt'],models:['test']});
                if (name === 'GPTListChats') return options.success([{conversation_id:cid,model:'test',title:'Chat'}]);
                if (name === 'GPTCreateChat') return options.success({conversation_id:cid,model:'test'});
                if (name === 'GPTFileChunk' || name === 'GPTFileCancel') return options.success({});
                if (name === 'GPTFileFinish') return options.success(file);
                if (name === 'GPTChat') {
                    if (fail) return options.success({status:'error',message:'offline'});
                    stored = true;
                    return options.success({response:'42',title:'Debt',model:'test',files:[]});
                }
                if (name === 'GPTAttachFiles') { stored = true; return options.success({files:[file]}); }
                if (name === 'GPTLoadChat') return options.success({history:stored?[{role:'user',content:'',files:[file]}]:[],files:stored?[file]:[]});
                throw new Error('Unexpected call ' + name);
            }, 0);
        }
    }};
    win.eval(script);
    await until(() => $('#chatStatus').text() === 'Аналитик готов к работе' && !$('#sendBtn').prop('disabled'));
    async function pick(kind) {
        const local = new win.File(['test'], 'debt.txt', {type:'text/plain'});
        if (kind === 'paste') {
            const e = $.Event('paste');
            e.originalEvent = {clipboardData:{files:[local], getData:()=>''},preventDefault(){}};
            $('#prompt').trigger(e);
        } else if (kind === 'drop') {
            const e = $.Event('drop');
            e.originalEvent = {dataTransfer:{files:[local]},preventDefault(){}};
            $('.composer').trigger(e);
        } else {
            Object.defineProperty($('#P237_GPT_FILES')[0], 'files', {configurable:true,value:[local]});
            $('#P237_GPT_FILES').trigger('change');
        }
        assert.equal($('#selectedFiles .selected-file').length, 1, 'preview must appear immediately');
        await until(() => !$('#sendBtn').prop('disabled'));
        assert.match($('#selectedFiles').text(), /Готов к отправке/);
    }
    return {dom,$,calls,pick,setFailure:value=>{fail=value;}};
}
(async () => {
    for (const kind of ['select','paste','drop']) {
        const f = await fixture();
        await f.pick(kind);
        f.$('#prompt').val('Сколько?');
        f.$('#sendBtn').trigger('click');
        await until(() => !f.$('#sendBtn').prop('disabled'));
        const request = f.calls.find(c => c.name === 'GPTChat');
        assert.equal(request.data.x01, 'Сколько?');
        assert.equal(request.data.x04, fid);
        assert.equal(f.$('#prompt').val(), '');
        assert.equal(f.$('#selectedFiles').children().length, 0);
        assert.equal(f.$('#chatFiles a').length, 1);
        assert.equal(f.$('#sendBtn svg').length, 1);
        f.$('#chatList .chat-item').trigger('click');
        await until(() => !f.$('#sendBtn').prop('disabled'));
        assert.equal(f.$('#chatMessages .msg.user a').length, 1, 'history must restore attachments');
        f.dom.window.close();
    }
    const only = await fixture();
    await only.pick('select');
    only.$('#sendBtn').trigger('click');
    await until(() => !only.$('#sendBtn').prop('disabled'));
    assert.equal(only.calls.filter(c=>c.name==='GPTChat').length, 0);
    assert.equal(only.calls.filter(c=>c.name==='GPTAttachFiles').length, 1);
    assert.doesNotMatch(only.$('#chatMessages').text(), /Проанализируй/);
    only.dom.window.close();
    const retry = await fixture();
    await retry.pick('select');
    retry.setFailure(true);
    retry.$('#prompt').val('Сохрани мой вопрос');
    retry.$('#sendBtn').trigger('click');
    await until(() => !retry.$('#sendBtn').prop('disabled'));
    assert.equal(retry.$('#prompt').val(), 'Сохрани мой вопрос');
    assert.equal(retry.$('#selectedFiles .selected-file').length, 1);
    retry.$('#chatList .chat-item').trigger('click');
    await until(() => !retry.$('#sendBtn').prop('disabled'));
    assert.equal(retry.$('#prompt').val(), 'Сохрани мой вопрос');
    assert.equal(retry.$('#selectedFiles .selected-file').length, 1);
    retry.setFailure(false);
    retry.$('#sendBtn').trigger('click');
    await until(() => !retry.$('#sendBtn').prop('disabled'));
    assert.equal(retry.calls.filter(c=>c.name==='GPTFileFinish').length, 1, 'retry must reuse uploaded file');
    assert.equal(retry.$('#prompt').val(), '');
    retry.dom.window.close();
    console.log('UI workflows passed: selection, paste, drop, attachment-only, failure/retry, history, draft and SVG.');
})().catch(error=>{ console.error(error); process.exitCode=1; });
