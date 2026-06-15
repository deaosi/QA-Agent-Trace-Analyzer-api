import sys
sys.stdout.reconfigure(encoding='utf-8')

# Read pre and post Python code
with open('E:\\2\\t1\\test01\\111\\templates\\_pre.py', 'rb') as f:
    pre = f.read()
with open('E:\\2\\t1\\test01\\111\\templates\\_post.py', 'rb') as f:
    post = f.read()

# Read index.html for CSS
with open('E:\\2\\t1\\test01\\111\\templates\\index.html', 'r', encoding='utf-8') as f:
    index_html = f.read()

css_start = index_html.find('<style>')
css_end = index_html.find('</style>') + len('</style>')
css = index_html[css_start:css_end]

# Build HTML content
html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QA Agent Trace Analyzer</title>
''' + css + r'''
</head>
<body>
<div class="header"><h1>QA Agent Trace Analyzer</h1><span>Agent Trace -> 话题分析 -> Q&A 案例</span></div>
<div class="container">

<div class="metrics">
  <div class="metric"><div class="v" id="m1">-</div><div class="l">已抓取 Trace</div></div>
  <div class="metric"><div class="v" id="m2">-</div><div class="l">识别话题</div></div>
  <div class="metric"><div class="v" id="m3">-</div><div class="l">Q&A 案例数</div></div>
</div>

<div class="card">
  <div class="card-header">Cookie 配置 <span style="font-weight:400;font-size:12px;color:#888">（填一次，自动保存）</span></div>
  <div class="card-body">
    <details open>
      <summary style="cursor:pointer;font-size:13px;color:#1a73e8;font-weight:500">Cookie 拼接助手</summary>
      <div style="margin-top:8px;padding:12px;background:#f8f9fa;border-radius:6px">
        <p style="font-size:12px;color:#888;margin-bottom:4px">agent.tanyuai.com -> F12 -> Application -> Cookies -> 全选复制 -> 粘贴到下面 -> 点"从表格粘贴"</p>
        <textarea id="cookieRaw" oninput="pasteTable()" placeholder="粘贴 Cookie 表格..." style="width:100%;min-height:50px;font-family:monospace;font-size:12px;padding:8px;border:1px solid #ddd;border-radius:6px"></textarea>
        <button class="btn btn-blue btn-sm" style="margin-top:8px" onclick="pasteTable()">从表格粘贴</button>
        <span id="cp" style="margin-left:12px;font-size:11px;color:#0d904f"></span>
      </div>
    </details>
    <div class="row" style="margin-top:12px">
      <div style="flex:3"><label>Cookie 字符串</label><textarea id="cookie" placeholder="粘贴到这里..."></textarea></div>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-header">店铺管理 <span id="shopStatus" style="font-weight:400;font-size:12px"></span></div>
  <div class="card-body">
    <div class="row">
      <div class="field" style="flex:2"><label>已保存店铺</label><select id="shopSelect" onchange="switchShop()"><option value="">-- 选择店铺 --</option></select></div>
      <div style="padding-top:20px"><button class="btn btn-sm btn-red" onclick="delShop()">删除</button></div>
    </div>
    <details style="margin-top:12px">
      <summary style="cursor:pointer;font-size:13px;color:#1a73e8;font-weight:500">批量导入店铺 ID（一行一个，自动探测）</summary>
      <div style="margin-top:8px">
        <textarea id="batchIds" placeholder="2605317072510000690&#10;..." style="width:100%;min-height:50px;font-family:monospace;font-size:12px;padding:8px;border:1px solid #ddd;border-radius:6px"></textarea>
        <button class="btn btn-blue btn-sm" style="margin-top:8px" onclick="batchProbe()">批量探测</button>
        <span id="batchStatus" style="margin-left:12px;font-size:12px;color:#888"></span>
      </div>
    </details>
    <details style="margin-top:8px" open>
      <summary style="cursor:pointer;font-size:13px;color:#1a73e8;font-weight:500">检索筛选条件（模拟后台筛选）</summary>
      <div class="row" style="margin-top:8px">
        <div class="field"><label>审核状态</label><select id="fReviewStatus"><option value="">全部</option><option value="0">未审核</option><option value="1" selected>已审核</option><option value="2">审核中</option></select></div>
        <div class="field"><label>是否标注</label><select id="fIfLabel"><option value="">全部</option><option value="0" selected>未标注</option><option value="1">已标注</option></select></div>
        <div class="field"><label>对话类型</label><select id="fType"><option value="">全部</option><option value="CONSULT_PRODUCT">商品咨询</option><option value="CONSULT_REPLY">咨询回复</option><option value="AFTER_SALE">售后</option><option value="COMPLAINT">投诉</option></select></div>
        <div class="field"><label>业务线</label><select id="fBusi"><option value="">全部</option><option value="RECEPTION" selected>接待</option><option value="AFTER_SALE">售后</option></select></div>
        <div class="field" style="flex:3;min-width:300px"><label>发送状态（可多选）</label><div style="display:flex;gap:12px;flex-wrap:wrap;padding-top:4px">
          <label style="font-size:12px;font-weight:400"><input type="checkbox" class="sendTypeCb" value="0" checked> 未发送</label>
          <label style="font-size:12px;font-weight:400"><input type="checkbox" class="sendTypeCb" value="1"> 自动发送</label>
          <label style="font-size:12px;font-weight:400"><input type="checkbox" class="sendTypeCb" value="2"> 侧边栏点击发送</label>
          <label style="font-size:12px;font-weight:400"><input type="checkbox" class="sendTypeCb" value="3"> 编辑后发送</label>
        </div></div>
      </div>
    </details>
  </div>
</div>

<div class="card">
  <div class="card-header">数据抓取与分析</div>
  <div class="card-body">
    <div class="row">
      <div class="field"><label>店铺 ID</label><input id="shopId" placeholder="如选中店铺则自动填充"></div>
      <div class="field"><label>开始时间</label><input type="datetime-local" id="beginTime"></div>
      <div class="field"><label>结束时间</label><input type="datetime-local" id="endTime"></div>
      <div class="field" style="max-width:80px"><label>每页条数</label><input id="pageSize" value="50"></div>
      <div class="field" style="max-width:80px"><label>最大页数</label><input id="maxPages" value="40"></div>
      <div style="padding-top:20px;display:flex;gap:8px">
        <button class="btn btn-go" id="goBtn" onclick="go()">一键分析</button>
        <button class="btn btn-red" id="stopBtn" style="display:none" onclick="stop()">停止</button>
        <label style="font-size:12px;font-weight:400;padding-top:10px"><input type="checkbox" id="overwriteCb"> 覆盖已有数据</label>
      </div>
    </div>

    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar-wrap"><div class="progress-bar-fill" id="progressBar"></div></div>
      <div class="progress-text"><span id="progressLabel"></span><span id="progressPct"></span></div>
    </div>

    <div class="log-area" id="log"></div>

    <div id="resultArea" style="margin-top:16px">
      <b id="resultSummary" style="font-size:14px"></b>
      <div id="resultBody" style="margin-top:12px"></div>
    </div>
  </div>
</div>

</div>

<script>
var currentShop="", abortCtrl=null;

function $(id){return document.getElementById(id)}
async function get(url){let r=await fetch(url);return r.json()}
async function post(url,data){let r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});return r.json()}
function log(msg){let e=$("log");e.style.display="block";e.innerHTML+=msg+"\\n";e.scrollTop=e.scrollHeight}
function setProgress(pct,label){$("progressBar").style.width=pct+"%";$("progressLabel").textContent=label||"";$("progressPct").textContent=pct+"%"}
function showResult(html){$("resultBody").innerHTML=html}

function pasteTable(){
  let raw=$("cookieRaw").value.trim(); if(!raw) return;
  let parts=[];
  raw.split("\\n").forEach(line=>{let cols=line.split("\\t");if(cols.length>=2) parts.push(cols[0].trim()+"="+cols[1].trim())});
  if(parts.length){$("cookie").value=parts.join("; ");$("cp").textContent="已拼接 "+parts.length+" 项"}
}

async function init(){
  let now=new Date();
  $("beginTime").value=now.getFullYear()+"-"+String(now.getMonth()+1).padStart(2,"0")+"-"+String(now.getDate()).padStart(2,"0")+"T00:00";
  $("endTime").value=now.getFullYear()+"-"+String(now.getMonth()+1).padStart(2,"0")+"-"+String(now.getDate()).padStart(2,"0")+"T23:59";
  let r=await get("/api/cookie-status");
  if(r.hasCookie){$("cookie").value="(已保存)";document.getElementById("shopStatus").innerHTML='<span class="s-ok">已加载</span>'}
  else document.getElementById("shopStatus").innerHTML='<span class="s-err">请配置 Cookie</span>';
  await loadShops();
}
init();

async function loadShops(){
  let r=await get("/api/shops");let sel=$("shopSelect");
  sel.innerHTML='<option value="">-- 选择店铺 --</option>';
  if(r.shops) r.shops.forEach(s=>{sel.innerHTML+='<option value="'+s.id+'">'+(s.name||s.id)+'</option>'});
  if(r.current){sel.value=r.current;$("shopId").value=r.current;currentShop=r.current}
}
async function switchShop(){
  let sid=$("shopSelect").value;if(!sid) return;
  $("shopId").value=sid;currentShop=sid;
  await post("/api/set-shop",{shopId:sid});refresh();
}
async function delShop(){
  let sid=$("shopSelect").value;if(!sid) return;
  if(!confirm("删除店铺 "+sid+" 的所有数据？")) return;
  await post("/api/delete-shop",{shopId:sid});loadShops();$("resultBody").innerHTML='<p style="color:#888">数据已删除</p>';
}
async function batchProbe(){
  let ids=$("batchIds").value.trim().split("\\n").map(s=>s.trim()).filter(Boolean);
  if(!ids.length) return;
  $("batchStatus").textContent="探测中...";
  for(let id of ids){
    let r=await post("/api/probe-shop",{shopId:id});
    $("batchStatus").textContent+=" "+id+": "+(r.name||"失败")+" |";
  }
  loadShops();
}

async function go(){
  let sid=$("shopId").value.trim()||currentShop;
  if(!sid) return alert("请选择或输入店铺 ID");
  $("shopId").value=sid;currentShop=sid;
  $("goBtn").style.display="none";$("stopBtn").style.display="inline-flex";$("progressWrap").style.display="block";
  $("log").style.display="block";$("log").innerHTML="";
  log("=== 店铺: "+sid+" ===");
  setProgress(10,"正在抓取数据...");

  let c=$("cookie").value;
  if(c&&c!="(已保存)") await post("/api/save-cookie",{cookie:c});

  let filters={};
  let fs=$("fReviewStatus").value; if(fs!=="") filters.reviewStatus=parseInt(fs);
  let fl=$("fIfLabel").value; if(fl!=="") filters.ifLabel=parseInt(fl);
  let sendTypes=[];
  document.querySelectorAll(".sendTypeCb:checked").forEach(cb=>sendTypes.push(parseInt(cb.value)));
  if(sendTypes.length>0) filters.sendType=sendTypes;

  abortCtrl=new AbortController();
  let overwrite=$("overwriteCb").checked;
  let f=await post("/api/fetch",{shopId:sid,beginTime:$("beginTime").value.replace("T"," ")+":00",endTime:$("endTime").value.replace("T"," ")+":00",pageSize:+$("pageSize").value,maxPages:+$("maxPages").value,filters:filters,overwrite:overwrite});

  if(f.log) f.log.forEach(l=>{if(l.status=="ok") log("[OK] 第"+l.page+"页 "+l.count+"条 (共"+l.total+"条)");else if(l.status=="error") log("[ERR] "+l.msg);else log("[空] 第"+l.page+"页")});
  if(f.shopName){log(">> 店铺: "+f.shopName);await loadShops();$("shopSelect").value=sid}
  log(">> 新增 "+f.totalFetched+" 条，累计 "+f.totalStored+" 条");

  if(!abortCtrl||abortCtrl.signal.aborted){$("stopBtn").style.display="none";$("goBtn").style.display="inline-flex";$("goBtn").disabled=false;$("goBtn").textContent="一键分析";abortCtrl=null;return}
  if(f.totalStored==0){log("没有数据！");$("progressWrap").style.display="none";$("stopBtn").style.display="none";$("goBtn").style.display="inline-flex";$("goBtn").disabled=false;$("goBtn").textContent="一键分析";abortCtrl=null;return}

  setProgress(55,"正在分析话题...");
  log(">> 话题分析 & 提取 Q&A...");
  let animTimer=setInterval(function(){let w=parseInt($("progressBar").style.width)||55;if(w<90) setProgress(w+1,"提取 Q&A 案例中...")},500);
  let a=await post("/api/analyze",{shopId:sid});
  clearInterval(animTimer);
  setProgress(90,"生成结果中...");

  if(!a.success||a.data.error){log("[ERR] "+(a.data?.error||""));$("progressWrap").style.display="none";$("stopBtn").style.display="none";$("goBtn").style.display="inline-flex";$("goBtn").disabled=false;$("goBtn").textContent="一键分析";abortCtrl=null;return}

  let d=a.data, html="";
  html+='<div style="margin-bottom:20px"><b style="font-size:14px">话题分布</b>';
  html+='<table style="margin-top:8px"><tr><th>话题</th><th>数量</th><th>占比</th></tr>';
  d.topicDistribution.forEach(t=>{
    let color=t.percentage>20?"#d93025":t.percentage>10?"#b06000":"#2e7d32";
    html+='<tr><td><b>'+t.topic+'</b></td><td>'+t.count+'</td><td>'+t.percentage+'%<div class="bar"><div class="bar-f" style="width:'+t.percentage+'%;background:'+color+'"></div></div></td></tr>';
  });
  html+='</table></div>';

  if(d.qaExamples){
    html+='<div><b style="font-size:14px">典型 Q&A 案例（按话题分类）</b>';
    let totalQA=0;
    d.qaExamples.forEach(topic=>{
      html+='<div style="margin-top:14px"><b style="font-size:13px;color:#1a73e8">'+topic.topic+'</b> <span style="font-size:11px;color:#888">('+topic.count+'条对话)</span>';
      topic.examples.forEach((ex,i)=>{
        html+='<div class="qa-block"><div class="qa-q"><span class="qlabel">Q:</span>'+ex.question+'</div><div class="qa-a"><span class="alabel">A:</span>'+ex.answer+'</div><div class="qa-meta">客服: '+ex.seller+' | 类型: '+ex.type+' | '+(ex.topicName||"")+'</div></div>';
        totalQA++;
      });
      html+='</div>';
    });
    html+='</div>';
    $("m3").textContent=totalQA;
  }

  $("resultBody").innerHTML=html;
  document.getElementById("resultSummary").textContent="共 "+d.totalRecords+" 条记录，"+d.topicDistribution.length+" 个话题";
  log(">> 完成！"+d.topicDistribution.length+" 个话题");

  setProgress(100,"全部完成！");
  setTimeout(function(){$("progressWrap").style.display="none"},2500);
  $("stopBtn").style.display="none";$("goBtn").style.display="inline-flex";
  $("goBtn").disabled=false;$("goBtn").textContent="一键分析";abortCtrl=null;
  refresh();
}

function stop(){
  if(abortCtrl){abortCtrl.abort();abortCtrl=null}
  $("stopBtn").style.display="none";$("goBtn").style.display="inline-flex";
  $("goBtn").disabled=false;$("goBtn").textContent="一键分析";
}

async function refresh(){
  let sid=currentShop||$("shopId").value;
  let r=await get("/api/overview?shopId="+(sid||""));
  $("m1").textContent=r.totalTraces||"-";
  $("m2").textContent=r.totalTopics||"-";
}
</script>
</body>
</html>'''

# Combine: pre (which already ends with HTML = r"""\n) + html + \n""" + post
result = pre.decode('utf-8') + html + '\n"""\n' + post.decode('utf-8')

with open('E:\\2\\t1\\test01\\111\\app.py', 'w', encoding='utf-8') as f:
    f.write(result)

print(f'Total: {len(result)} chars')
print('Done')
