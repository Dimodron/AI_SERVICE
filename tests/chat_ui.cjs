// NODE_PATH must contain jsdom and jquery. No Oracle/network requests are made.
const {JSDOM} = require('jsdom');
const jquery = require('jquery');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modules = ['common','chat','prompts','scenarios','import'];
const scripts = Object.fromEntries(modules.map(name => [name, fs.readFileSync(path.join(process.env.CHAT_UI_DIR || path.join(__dirname, '../oracle_serv'), name + '.js'), 'utf8')]));
const cid = '11111111-1111-4111-8111-111111111111';
const fid = '22222222-2222-4222-8222-222222222222';
const file = {file_id: fid, filename: 'debt.txt', size_bytes: 4};
const delay = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(check) {
    for (let i = 0; i < 200; i++) { if (check()) return; await delay(); }
    throw new Error('UI timeout');
}
async function fixture(admin = true) {
    const dom = new JSDOM(`<main id="chat_body"><div class="head-actions"></div><div class="chathead"></div><div id="chatStatus"></div><div id="chatTitle"></div><div id="chatMessages"></div><div id="chatList"></div><input id="chatSearch"><button id="newChatBtn"><span class="new-chat-plus"></span></button><div class="composer"><div id="selectedFiles"></div><textarea id="prompt"></textarea><button id="attachBtn"></button><button id="sendBtn"></button></div><input type="file" id="P237_GPT_FILES"><div class="hint"></div></main>`, {runScripts:'outside-only', url:'http://example.test'});
    const win = dom.window, $ = jquery(win);
    if (admin) {
        $("body").append('<div id="gptAdminControls"><button id="gptAdminGear"></button><div id="gptAdminMenu" style="display:none"><button data-admin-list="prompts">Prompts</button><button data-admin-list="scenarios">Scenarios</button><button data-admin-import>Import</button></div></div>');
        for (const name of ['prompts_list','prompts_edit','scenarios_list','scenarios_edit','export_list']) $("body").append(fs.readFileSync(path.join(__dirname,'../oracle_serv/admin_regions/'+name+'.html'),'utf8'));
    }
    const adminRows = {prompts:[], scenarios:[]};
    let active = '', fail = false, stored = false, model = 'test', lastQuestion = null;
    const calls = [];
    win.$v = () => active;
    win.$s = (_name, value) => {active = value;};
    win.apex = {jQuery:$, server:{
        url: args => '/download?id=' + args.x01,
        process: (name, data, options) => {
            calls.push({name, data});
            if (name === 'GPTChat' || name === 'GPTAttachFiles') {
                assert.equal($('#prompt').val(), '', 'composer clears before server response');
                assert.equal($('#selectedFiles').children().length, 0, 'attachments clear before server response');
                assert.equal($('#sendBtn').prop('disabled'), true, 'prevent double submit');
            }
            setTimeout(() => {
                if (name === 'GPTConfig') return options.success({max_files:5,max_file_bytes:5242880,extensions:['.txt'],default_model:'test',can_select_model:admin,models:admin?['test','second']:[]});
                if (name === 'GPTScenarioCatalog') {
                    if (fail) return options.success({status:'error',message:'catalog offline'});
                    if (data.x01==='schemas') return options.success(data.x04==='1' ? ['oracle_data','public'] : ['oracle_data']);
                    if (data.x01==='tables') return options.success([{name:'messages'},{name:'conversations'}]);
                    if (data.x01==='columns') return options.success({table_name:data.x02+'.'+data.x03,description:'Table comment',columns:[{name:'id',data_type:'uuid',nullable:false,description:'Identifier'},{name:'content',data_type:'text',nullable:true,description:'Text'}]});
                }
                if (name === 'GPTImportHistory') return options.success({
                    items:[{table_name:'oracle_data.zv_data',finished_at:'2026-09-28T10:00:00Z',filters:{version:'570534214'},mode:'replace',status:'success',row_count:42}],
                    last_success_at:'2026-09-28T10:00:00Z',has_more:false
                });
                if (name === 'GPTImportTables') return options.success({items:[{display_value:'ZV_DATA',return_value:'ZV_DATA'}],has_more:false});
                if (name === 'GPTImportRun') return options.success(fail ? {status:'error',message:'import failed'} : {tables:[{table:'oracle_data.zv_data',rows:42}]});
                if (name === 'GPTAdminList') return options.success(adminRows[data.x01]);
                if (name === 'GPTAdminSave') {
                    if (fail) return options.success({status:'error',message:'save failed'});
                    const body=JSON.parse(data.f01.join(''));
                    const row={...body,id:data.x02 || 'admin-record'};
                    adminRows[data.x01]=[row];
                    return options.success(row);
                }
                if (name === 'GPTListChats') return options.success([{conversation_id:cid,model:'test',title:'Chat'}]);
                if (name === 'GPTCreateChat') return options.success({conversation_id:cid,model:'test'});
                if (name === 'GPTFileChunk' || name === 'GPTFileCancel') return options.success({});
                if (name === 'GPTFileFinish') return options.success(file);
                if (name === 'GPTSetModel') {
                    if (fail) return options.success({status:'error',message:'no vision'});
                    model = data.x02;
                    return options.success({conversation_id:cid,model,title:'Chat'});
                }
                if (name === 'GPTChat') {
                    lastQuestion={role:'user',content:data.x01,files:data.x04?[file]:[],request_id:data.x05};
                    stored=true;
                    if (fail) return options.success({status:'error',message:'offline'});
                    stored = true;
                    return options.success({response:'42',title:'Debt',model,files:[]});
                }
                if (name === 'GPTAttachFiles') { stored = true; return options.success({files:[file]}); }
                if (name === 'GPTLoadChat') return options.success({model,history:stored?(lastQuestion?[lastQuestion]:[{role:'user',content:'',files:[file]}]):[],files:stored?[file]:[]});
                throw new Error('Unexpected call ' + name);
            }, 0);
        }
    }};
    for (const name of modules) {
        if (admin || !['prompts','scenarios','import'].includes(name)) win.eval(scripts[name]);
    }
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
    assert.equal(retry.$('#prompt').val(), '');
    assert.equal(retry.$('#selectedFiles .selected-file').length, 0);
    assert.equal(retry.$('#chatMessages .msg.user').length, 1);
    assert.equal(retry.$('.gpt-retry').length, 1);
    const firstKey=retry.calls.find(c=>c.name==='GPTChat').data.x05;
    retry.$('#chatList .chat-item').trigger('click');
    await until(() => !retry.$('#sendBtn').prop('disabled'));
    assert.equal(retry.$('.gpt-retry').length, 1,'retry survives reloading history');
    retry.setFailure(false);
    retry.$('.gpt-retry').trigger('click');
    await until(() => !retry.$('#sendBtn').prop('disabled'));
    assert.equal(retry.calls.filter(c=>c.name==='GPTChat').at(-1).data.x05,firstKey);
    assert.equal(retry.calls.filter(c=>c.name==='GPTFileFinish').length, 1);
    assert.equal(retry.$('#chatMessages .msg.user').length, 1);
    assert.equal(retry.$('.gpt-retry').length, 0);
    retry.dom.window.close();
    const ordinary = await fixture(false);
    assert.equal(ordinary.$('#gptModel').css('display'), 'none');
    assert.equal(ordinary.$('#gptModel').prop('disabled'), true);
    ordinary.$('#gptModel').val('second').trigger('change');
    assert.equal(ordinary.calls.filter(c=>c.name==='GPTSetModel').length, 0);
    ordinary.$('#prompt').val('Вопрос');
    ordinary.$('#sendBtn').trigger('click');
    await until(() => !ordinary.$('#sendBtn').prop('disabled'));
    assert.equal(ordinary.calls.find(c=>c.name==='GPTCreateChat').data.x01, '');
    assert.equal(ordinary.calls.filter(c=>c.name==='GPTChat').length, 1);
    ordinary.dom.window.close();
    const switching = await fixture();
    await switching.pick('select');
    switching.$('#prompt').val('Черновик');
    assert.equal(switching.$('#gptModel').prop('disabled'), false);
    switching.$('#gptModel').val('second').trigger('change');
    assert.equal(switching.$('#sendBtn').prop('disabled'), true);
    await until(() => !switching.$('#sendBtn').prop('disabled'));
    assert.equal(switching.$('#gptModel').val(), 'second');
    assert.equal(switching.$('#chatList .chat-item').attr('data-model'), 'second');
    assert.equal(switching.$('#prompt').val(), 'Черновик');
    assert.equal(switching.$('#selectedFiles .selected-file').length, 1);
    switching.setFailure(true);
    switching.$('#gptModel').val('test').trigger('change');
    await until(() => !switching.$('#sendBtn').prop('disabled'));
    assert.equal(switching.$('#gptModel').val(), 'second', 'failure must restore previous selection');
    switching.$('#chatList .chat-item').trigger('click');
    await until(() => !switching.$('#sendBtn').prop('disabled'));
    assert.equal(switching.$('#gptModel').val(), 'second', 'reopening must use saved model');
    switching.dom.window.close();
    const management=await fixture();
    const m=management.$;
    m('#prompt').val('Chat draft');
    m('#gptAdminGear').trigger('click');
    assert.equal(m('#gptAdminGear').attr('aria-expanded'),'true');
    m('[data-admin-list="prompts"]').trigger('click');
    await until(()=>!m('[data-admin-new="prompts"]').prop('disabled'));
    m('[data-admin-new="prompts"]').trigger('click');
    const longPrompt='Правило '.repeat(1500);
    m('#gpt-prompts-edit [data-field="prompt"]').val(longPrompt);
    m('[data-admin-save="prompts"]').trigger('click');
    await until(()=>m('#gpt-prompts-list .gpt-admin-row').length===1 && !m('[data-admin-new="prompts"]').prop('disabled'));
    let saved=management.calls.filter(c=>c.name==='GPTAdminSave').at(-1);
    assert.equal(JSON.parse(saved.data.f01.join('')).prompt,longPrompt.trim());
    assert.ok(saved.data.f01.length>1,'large Unicode bodies use chunks');
    m('#gpt-prompts-list .gpt-admin-row button').trigger('click');
    m('#gpt-prompts-edit [data-field="prompt"]').val('Changed');
    management.setFailure(true);
    m('[data-admin-save="prompts"]').trigger('click');
    await until(()=>m('#gpt-prompts-edit .gpt-admin-status').text()==='save failed');
    assert.equal(m('#gpt-prompts-edit [data-field="prompt"]').val(),'Changed');
    management.setFailure(false);
    m('[data-admin-save="prompts"]').trigger('click');
    await until(()=>m('#gpt-prompts-list').css('display')!=='none' && !m('[data-admin-new="prompts"]').prop('disabled'));
    saved=management.calls.filter(c=>c.name==='GPTAdminSave').at(-1);
    assert.equal(saved.data.x02,'admin-record');
    m('#gpt-prompts-list [data-admin-close]').trigger('click');
    assert.equal(m('#prompt').val(),'Chat draft');
    m('[data-admin-list="scenarios"]').trigger('click');
    await until(()=>!m('[data-admin-new="scenarios"]').prop('disabled'));
    m('[data-admin-new="scenarios"]').trigger('click');
    m('#gpt-scenarios-edit [data-field="title"]').val('Analyse');
    m('#gpt-scenarios-edit [data-field="scenario"]').val('Read messages');
    m('#gpt-scenarios-edit [data-field="groups"]').val('Admin, Analytics');
    m('#gpt-scenarios-edit [data-field="is_admin"]').prop('checked',true);
    for(const table of ['messages','conversations']) {
        m('[data-admin-add-table]').trigger('click');
        const row=m('.gpt-admin-table').last();
        await until(()=>row.find('[data-table-field="schema"] option').length>1);
        row.find('[data-table-field="schema"]').val('public').trigger('change');
        await until(()=>row.find('[data-table-field="table"] option').length>1);
        row.find('[data-table-field="table"]').val(table).trigger('change');
        await until(()=>row.find('.gpt-column-row').length===2);
        assert.match(row.text(),/uuid/);
        row.find('[data-table-field="description"]').val('Description');
        row.find('.gpt-column-description').first().val('My identifier');
        row.find('.gpt-column-use').last().prop('checked',false);
    }
    m('[data-admin-save="scenarios"]').trigger('click');
    await until(()=>m('#gpt-scenarios-list .gpt-admin-row').length===1 && !m('[data-admin-new="scenarios"]').prop('disabled'));
    saved=JSON.parse(management.calls.filter(c=>c.name==='GPTAdminSave').at(-1).data.f01.join(''));
    assert.equal(saved.tables.length,2);
    assert.deepEqual(saved.tables[0].columns_description,{id:'My identifier'});
    m('#gpt-scenarios-list .gpt-admin-row button').trigger('click');
    await until(()=>m('#gpt-scenarios-edit .gpt-column-row').length===4);
    assert.equal(m('.gpt-column-description').first().val(),'My identifier','saved description survives metadata fetch');
    assert.equal(m('.gpt-column-use').eq(1).prop('checked'),false,'unselected columns stay unselected');
    m('#gpt-scenarios-edit [data-field="is_admin"]').prop('checked',false).trigger('change');
    await until(()=>m('.gpt-table-status').first().text().includes('недоступна'));
    assert.equal(m('[data-table-field="schema"] option[value="public"]').length,0);
    assert.equal(m('.gpt-column-row').length,0);
    const modeSaves=management.calls.filter(c=>c.name==='GPTAdminSave').length;
    m('[data-admin-save="scenarios"]').trigger('click');
    assert.equal(management.calls.filter(c=>c.name==='GPTAdminSave').length,modeSaves);
    m('#gpt-scenarios-edit [data-field="is_admin"]').prop('checked',true).trigger('change');
    await until(()=>m('.gpt-column-row').length===4);
    assert.equal(m('.gpt-column-description').first().val(),'My identifier');
    assert.equal(m('.gpt-column-use').eq(1).prop('checked'),false);
    assert.ok(management.calls.some(c=>c.name==='GPTScenarioCatalog' && c.data.x04==='0'));
    management.setFailure(true);
    m('.gpt-admin-table button').filter(function(){return m(this).text()==='Обновить структуру';}).first().trigger('click');
    await until(()=>m('.gpt-table-status').first().text()==='catalog offline');
    const beforeSaves=management.calls.filter(c=>c.name==='GPTAdminSave').length;
    m('[data-admin-save="scenarios"]').trigger('click');
    assert.equal(management.calls.filter(c=>c.name==='GPTAdminSave').length,beforeSaves,'do not save incomplete metadata');
    management.setFailure(false);
    m('.gpt-admin-table button').filter(function(){return m(this).text()==='Обновить структуру';}).first().trigger('click');
    await until(()=>m('.gpt-column-row').length===4);
    assert.equal(m('.gpt-column-description').first().val(),'My identifier');

    assert.equal(saved.is_admin,true);
    assert.deepEqual(saved.groups,['Admin','Analytics']);
    m('[data-admin-import]').trigger('click');
    await until(()=>m('#gpt-import-table option').length===1);
    assert.notEqual(m('#gpt-import-list').css('display'),'none');
    m('#gpt-import-table').val('ZV_DATA').trigger('change');
    await until(()=>m('#gpt-import-history tr').length===1);
    assert.match(m('#gpt-import-history').text(),/570534214/);
    assert.match(m('#gpt-import-last-success').text(),/Последний успешный перенос/);
    m('#gpt-import-version').val('570534214');
    m('#gpt-import-field').val('jur_pers');
    m('#gpt-import-value').val('1351099');
    m('#gpt-import-mode').val('append');
    m('#gpt-import-run').trigger('click');
    m('#gpt-import-run').trigger('click');
    await until(()=>m('#gpt-import-result').text().includes('42'));
    let imports=management.calls.filter(c=>c.name==='GPTImportRun');
    assert.equal(imports.length,1,'prevent duplicate import submission');
    assert.ok(management.calls.some(c=>c.name==='GPTImportHistory' && c.data.x01==='ZV_DATA'));
    assert.equal(imports[0].data.x02,'570534214');
    assert.equal(imports[0].data.x03,'jur_pers');
    assert.equal(imports[0].data.x04,'1351099');
    assert.equal(imports[0].data.x05,'append');
    m('#gpt-import-field').val('VERSION');
    m('#gpt-import-run').trigger('click');
    assert.match(m('#gpt-import-result').text(),/дважды/);
    assert.equal(management.calls.filter(c=>c.name==='GPTImportRun').length,1);
    m('#gpt-import-field').val('jur_pers');
    management.setFailure(true);
    m('#gpt-import-run').trigger('click');
    await until(()=>m('#gpt-import-result').text()==='import failed');
    assert.equal(m('#gpt-import-table').val(),'ZV_DATA');
    assert.equal(m('#gpt-import-version').val(),'570534214');
    assert.equal(m('#gpt-import-run').prop('disabled'),false);
    management.setFailure(false);
    m('#gpt-import-version').val('');
    m('#gpt-import-field').val('');
    m('#gpt-import-value').val('');
    m('#gpt-import-run').trigger('click');
    await until(()=>m('#gpt-import-result').text().includes('42'));
    m('#gpt-import-list [data-admin-close]').trigger('click');
    assert.equal(m('#prompt').val(),'Chat draft');
    management.dom.window.close();
    const restricted=await fixture(false);
    assert.equal(restricted.$('#gptAdminGear').length,0);
    assert.equal(restricted.calls.filter(c=>c.name==='GPTAdminList').length,0);
    restricted.dom.window.close();
    console.log('UI workflows passed: selection, paste, drop, attachment-only, failure/retry, history, draft, SVG and model switching.');
})().catch(error=>{ console.error(error); process.exitCode=1; });
