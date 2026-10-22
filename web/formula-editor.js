(() => {
  let context=null, session=0, menuTarget=null, preferences={content:{},structure:{},epoch:0};
  let sync=Promise.resolve();
  const menu=document.createElement('div');menu.id='formula-menu';menu.className='formula-menu';menu.hidden=true;
  menu.setAttribute('role','dialog');menu.setAttribute('aria-label','修改公式写法');document.body.append(menu);
  const api=()=>window.pywebview?.api;
  const render=(tex,display=false)=>katex.renderToString(tex,{displayMode:display,throwOnError:true,trust:false,strict:false,output:'htmlAndMathml'});
  function close(focus=false) {menu.hidden=true;menu.replaceChildren();if(focus&&menuTarget?.isConnected)menuTarget.focus({preventScroll:true});menuTarget=null;}
  function placement(node) {
    const r=node.getBoundingClientRect(), reading=document.getElementById('reading').getBoundingClientRect();
    const top=Math.max(5,reading.top),bottom=innerHeight-6, gap=5;
    const above=r.top-top-gap,below=bottom-r.bottom-gap;
    const width=Math.min(330,innerWidth-12);
    menu.style.width=width+'px';menu.style.left=Math.max(6,Math.min(innerWidth-width-6,r.left))+'px';
    const down=below>=above;
    const room=Math.max(20,down?below:above);
    menu.style.maxHeight=room+'px';menu.style.top='0px';
    menu.style.top=(down?r.bottom+gap:Math.max(top,r.top-gap-Math.min(menu.scrollHeight,room)))+'px';
  }
  function observations(ctx) {
    return [...ctx.units.values()].map(u=>({index:u.part.index,offset:u.offset,end:u.end,partTemplate:u.partTemplate,template:u.template,tex:u.tex,content:u.unit.content,structure:u.unit.structure,selected:u.selected,corrected:u.corrected}));
  }
  function text(ctx) {
    let cursor=0,result='';
    for(const part of ctx.parts.filter(p=>p.type==='math'&&p.start>=0).sort((a,b)=>a.start-b.start)) {
      result+=ctx.source.slice(cursor,part.start);
      const change=ctx.changes.get(part.index);
      result+=change===undefined?ctx.source.slice(part.start,part.end):(part.display?'$$'+change+'$$':'$'+change+'$');
      cursor=part.end;
    }
    return result+ctx.source.slice(cursor);
  }
  function notify(ctx) {
    const updated=text(ctx);ctx.update(updated);
    const payload={entry:ctx.entry, before:ctx.saved,translation:updated,units:observations(ctx),epoch:preferences.epoch};
    ctx.saved=updated;
    // Preserve edit ordering even if the bridge dispatches callbacks on separate threads.
    sync=sync.then(()=>api()?.formula_update?.(payload)).then(result=>{
      if(result?.error && context===ctx)ctx.notice(result.error);
    }).catch(()=>{if(context===ctx)ctx.notice('公式已修改，保存暂未完成。');});
  }
  function choose(ctx,u,tex,id) {
    if(context!==ctx || !u.node.isConnected)return;
    try{render(tex);}catch(_){return;}
    const oldTop=document.getElementById('reading').scrollTop;
    const previous={tex:u.tex,selected:u.selected,corrected:[...u.corrected]};
    const previousChange=ctx.changes.get(u.part.index);
    if(tex!==u.tex)u.corrected.push(u.selected);
    u.selected=id;u.tex=tex;
    try{rebuild(ctx,u.part,u);}catch(_){
      Object.assign(u,previous);
      if(previousChange===undefined)ctx.changes.delete(u.part.index);else ctx.changes.set(u.part.index,previousChange);
      close();ctx.notice('这处写法暂时无法应用，已保留原公式。');return;
    }
    close();ctx.overflow();document.getElementById('reading').scrollTop=oldTop;
    notify(ctx);
  }
  function open(ctx,u) {
    if(context!==ctx)return;
    close();menuTarget=u.node;
    const heading=document.createElement('div');heading.className='formula-menu-title';heading.textContent='选择公式写法';menu.append(heading);
    const variants=[...u.unit.variants];
    if(!variants.some(v=>v.tex===u.tex))variants.push({id:'custom',tex:u.tex,label:'当前手写公式'});
    variants.sort((a,b)=>score(u.unit,b.id)-score(u.unit,a.id));
    for(const v of variants){
      const button=document.createElement('button');button.className='formula-choice';button.type='button';
      button.dataset.style=v.id;
      button.setAttribute('role','menuitemradio');button.setAttribute('aria-checked',String(v.tex===u.tex));
      const mark=document.createElement('span');mark.textContent=v.tex===u.tex?'✓':'';
      const preview=document.createElement('span');try{preview.innerHTML=render(v.tex);}catch(_){continue;}
      const label=document.createElement('small');label.textContent=v.label;button.append(mark,preview,label);
      button.addEventListener('click',()=>choose(ctx,u,v.tex,v.id));menu.append(button);
    }
    const manual=document.createElement('button');manual.type='button';manual.className='formula-manual';
    manual.textContent='上述公式变换均不对，我自己来输入（仅支持 LaTeX，需要使用者熟悉 LaTeX）';
    manual.addEventListener('click',()=>custom(ctx,u));menu.append(manual);
    menu.hidden=false;placement(u.node);
    menu.querySelector('button')?.focus({preventScroll:true});
  }
  function custom(ctx,u) {
    menu.replaceChildren();
    const help=document.createElement('p');help.textContent='输入裸 LaTeX 公式。不要带 $、$$、\\[…\\] 或 \\(…\\)。';
    const input=document.createElement('textarea');input.value=u.tex;input.maxLength=4000;input.setAttribute('aria-label','裸 LaTeX 公式');
    const preview=document.createElement('div');preview.className='formula-preview';
    const error=document.createElement('p');error.className='formula-input-error';error.setAttribute('role','status');
    const apply=document.createElement('button');apply.textContent='应用';apply.type='button';
    const back=document.createElement('button');back.textContent='返回写法列表';back.type='button';
    function update(){
      const value=input.value.trim();let message='';
      if(!value)message='请输入公式。';
      else if(/\$|\\[\[\]\(\)]/.test(value))message='请去掉公式外层的定界符。';
      try{if(!message)preview.innerHTML=render(value,u.part.display);}catch(_){message='LaTeX 语法有误，请检查括号与命令。';}
      if(message)preview.replaceChildren();error.textContent=message;apply.disabled=!!message;
    }
    input.addEventListener('input',update);apply.addEventListener('click',()=>choose(ctx,u,input.value.trim(),'custom'));
    back.addEventListener('click',()=>open(ctx,u));menu.append(help,input,preview,error,apply,back);update();placement(u.node);input.focus({preventScroll:true});
  }
  function score(unit,id){return 4*(preferences.content[unit.content]?.[id]||0)+.35*(preferences.structure[unit.structure]?.[id]||0);}
  function rebuild(ctx,part,changed=null){
    const members=[...ctx.units.values()].filter(u=>u.part===part).sort((a,b)=>a.offset-b.offset);
    const whole=members.length===1&&members[0].offset===0&&members[0].end===members[0].partTemplate.length;
    const template=members[0]?.partTemplate||part.tex;
    let value='',cursor=0;
    const previous=new Map();
    for(const u of members){
      const prefix=template.slice(cursor,u.offset);value+=prefix+u.tex;
      if(changed&&u!==changed&&u.node?.isConnected)previous.set(u,u.node);
      cursor=u.end;
    }
    value+=template.slice(cursor);
    ctx.changes.set(part.index,value);
    const node=ctx.el.querySelector(`[data-formula-part="${part.index}"]`);
    const full=document.createElement('span');full.innerHTML=render(value,part.display);
    if(whole){node.replaceChildren(...full.childNodes);members[0].node=node;enable(ctx,members[0],node);return;}
    const used=new Set([...full.querySelectorAll('.katex-html [style]')].map(n=>n.style.color));
    const color=document.createElement('span');let palette=0x100001,marked='',wrapped='';cursor=0;
    for(const u of members){
      // Temporary colors locate each rendered unit at any nesting depth. They
      // require no trusted HTML commands and stay in an offscreen render.
      do {u.marker='#'+(palette++).toString(16);color.style.color=u.marker;}while(used.has(color.style.color));
      used.add(color.style.color);
      const prefix=template.slice(cursor,u.offset);
      marked+=prefix+`\\textcolor{${u.marker}}{\\mathord{${u.tex}}}`;
      wrapped+=prefix+`\\mathord{${u.tex}}`;cursor=u.end;
    }
    marked+=template.slice(cursor);wrapped+=template.slice(cursor);
    const locator=document.createElement('span');locator.innerHTML=render(marked,part.display);
    const display=document.createElement('span');display.innerHTML=render(wrapped,part.display);
    display.querySelector('.katex-mathml').replaceWith(full.querySelector('.katex-mathml'));
    const located=[...locator.querySelectorAll('.katex-html *')],visible=[...display.querySelectorAll('.katex-html *')];
    // KaTeX color scopes do not add HTML elements. Keep source colors in the
    // visible render and use the parallel tree only to identify each unit.
    if(located.length!==visible.length||located.some((n,i)=>n.tagName!==visible[i].tagName))throw new Error('Formula unit mapping unavailable');
    for(const u of members){
      const color=document.createElement('span');color.style.color=u.marker;
      const index=located.findIndex(n=>n.style.color===color.style.color),target=visible[index];
      if(!target)continue;
      const old=previous.get(u);
      if(old){target.replaceWith(old);u.node=old;}
      else {u.node=target;enable(ctx,u,target);}
    }
    node.replaceChildren(...display.childNodes);
  }
  function enable(ctx,u,node){
    node.dataset.formulaOffset=u.offset;node.dataset.formulaTemplate=u.template;
    node.classList.add('formula-editable');node.tabIndex=0;node.title='右键修改公式';node.setAttribute('aria-label','公式，右键修改公式');
    node.addEventListener('contextmenu',ev=>{ev.preventDefault();ev.stopPropagation();open(ctx,u);});
    node.addEventListener('keydown',ev=>{if(ev.key==='Enter'||(ev.shiftKey&&ev.key==='F10')){ev.preventDefault();ev.stopPropagation();open(ctx,u);}});
  }
  window.FormulaEditor={
    reset(){++session;close();context=null;},
    flush(){return sync;},
    endObservation(){if(context)for(const u of context.units.values())u.corrected=[];},
    async attach(el,source,parts,entry,reviewed,update,notice,overflow,savedUnits=[]){
      const current=++session;close();context=null;
      try{preferences=await api()?.formula_preferences?.()||{content:{},structure:{},epoch:0};}catch(_){preferences={content:{},structure:{},epoch:0};}
      if(current!==session)return;
      const ctx=context={el,source,parts,entry,update,notice,overflow,saved:source,changes:new Map(),units:new Map()};
      let warned=false;
      for(const part of parts){
        if(part.type!=='math'||part.start<0)continue;
        const prior=part.grouped?[]:savedUnits.filter(u=>u.index===part.index);
        const partTemplate=prior[0]?.partTemplate||prior[0]?.template||part.tex;
        const root=el.querySelector(`[data-formula-part="${part.index}"]`);
        if(!root)continue;
        try{
        const members=[];
        for(const fragment of FormulaUnits.scan(partTemplate)){
        const saved=prior.find(u=>(u.offset||0)===fragment.start),template=fragment.tex,unit=fragment.unit;
        const remembered=preferences.custom?.[unit.content];
        if(remembered&&!unit.variants.some(v=>v.tex===remembered))unit.variants.push({id:'custom',tex:remembered,label:'记住的手写公式'});
        const u={unit,node:root,part,partTemplate,template,offset:fragment.start,end:fragment.end,tex:saved?.tex||template,selected:saved?.selected||unit.current,corrected:[]};
        ctx.units.set(part.index+':'+fragment.start,u);members.push(u);
        // Preserve the API's writing (or an explicit saved edit). Preferences
        // rank the menu only and must never silently replace correct scripts.
        }
        if(members.length)rebuild(ctx,part);
        }catch(_){
          // One unsupported editing boundary must not disable the entire card.
          // Rendering already succeeded before attachment, so restore that math.
          for(const [key,u] of ctx.units)if(u.part===part)ctx.units.delete(key);
          ctx.changes.delete(part.index);
          root.innerHTML=render(part.tex,part.display);
          if(!warned){warned=true;notice('部分公式暂不可调整，已保留原始显示。');}
        }
      }
      overflow();if(ctx.units.size)notify(ctx);
    },
  };
  document.addEventListener('pointerdown',e=>{if(!menu.hidden&&!menu.contains(e.target))close();});
  document.getElementById('reading').addEventListener('scroll',()=>close(),{passive:true});
  window.addEventListener('resize',()=>close());
  document.addEventListener('keydown',e=>{
    if(menu.hidden)return;
    if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();close(true);}
    else if(!['TEXTAREA','INPUT'].includes(e.target.tagName)&&['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
      e.preventDefault();const items=[...menu.querySelectorAll('button:not(:disabled)')],i=items.indexOf(document.activeElement);
      items[e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();
    }
  },true);
})();
