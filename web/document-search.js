/* Search the rendered translation without rewriting its editable DOM. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const doc=$('doc'),bar=$('find-bar'),input=$('find-query'),count=$('find-count');
  const previous=$('find-prev'),next=$('find-next'),toggle=$('btn-find');
  const blocks=new Set(['P','DIV','LI','UL','OL','PRE','BLOCKQUOTE','H1','H2','H3','H4','H5','H6','TR','TD','TH','FIGURE']);
  let hits=[],current=-1,timer=0,callbacks={};
  const nativeHighlights=!!(window.CSS?.highlights&&window.Highlight);
  const overlay=document.createElement('div');overlay.className='find-overlay';overlay.setAttribute('aria-hidden','true');document.body.append(overlay);

  function occurrences(text) {
    const query=input.value;
    if(!query)return [];
    const pattern=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),$('find-case').checked?'gu':'giu');
    const found=[];
    for(const match of text.matchAll(pattern)) {
      const start=match.index,end=start+match[0].length;
      if($('find-word').checked && (/[\p{L}\p{M}\p{N}_]$/u.test(text.slice(0,start)) || /^[\p{L}\p{M}\p{N}_]/u.test(text.slice(end))))continue;
      found.push({start,end});
    }
    return found;
  }

  function collect() {
    const groups=[];let text='',nodes=[];
    function flush(formula=null) {
      if(nodes.length||formula)groups.push({text,nodes,formula});
      text='';nodes=[];
    }
    function visit(node,insideMath=false) {
      if(node.nodeType===Node.TEXT_NODE) {
        nodes.push({node,start:text.length,end:text.length+node.length});text+=node.data;return;
      }
      if(node.nodeType!==Node.ELEMENT_NODE)return;
      if(node.hidden||node.matches('.katex-mathml, annotation, script, style'))return;
      if(node.matches('.math-scroll')&&!insideMath) {
        flush();for(const child of node.childNodes)visit(child,true);flush(node);return;
      }
      const boundary=!insideMath&&(blocks.has(node.tagName)||node.tagName==='BR');
      if(boundary)flush();
      for(const child of node.childNodes)visit(child,insideMath);
      if(boundary)flush();
    }
    visit(doc);flush();return groups;
  }

  function rangeFor(group,match) {
    const first=group.nodes.find(n=>n.end>match.start);
    const last=group.nodes.find(n=>n.end>=match.end);
    if(!first||!last)return null;
    const range=new Range();range.setStart(first.node,match.start-first.start);range.setEnd(last.node,match.end-last.start);
    return range;
  }

  function clearPaint() {
    if(nativeHighlights){CSS.highlights.delete('translation-find');CSS.highlights.delete('translation-current');}
    overlay.replaceChildren();
  }

  function paint() {
    clearPaint();
    if(bar.hidden||doc.hidden)return;
    if(nativeHighlights) {
      const all=new Highlight();for(const hit of hits)all.add(hit.range);
      CSS.highlights.set('translation-find',all);
      if(hits[current]){const active=new Highlight(hits[current].range);active.priority=1;CSS.highlights.set('translation-current',active);}
    } else {
      // Older WebView2 runtimes use clipped overlays, keeping KaTeX nodes intact.
      const reading=$('reading').getBoundingClientRect();
      for(let i=0;i<hits.length;i++) {
        let clip={left:reading.left,right:reading.right,top:reading.top,bottom:reading.bottom};
        for(let node=hits[i].element;node&&node!==doc;node=node.parentElement) {
          const style=getComputedStyle(node),r=node.getBoundingClientRect();
          if(/auto|scroll|hidden/.test(style.overflowX)){clip.left=Math.max(clip.left,r.left);clip.right=Math.min(clip.right,r.right);}
          if(/auto|scroll|hidden/.test(style.overflowY)){clip.top=Math.max(clip.top,r.top);clip.bottom=Math.min(clip.bottom,r.bottom);}
        }
        for(const rect of hits[i].range.getClientRects()) {
          const left=Math.max(rect.left,clip.left),top=Math.max(rect.top,clip.top);
          const width=Math.min(rect.right,clip.right)-left,height=Math.min(rect.bottom,clip.bottom)-top;
          if(width<=0||height<=0)continue;
          const mark=document.createElement('span');mark.className=i===current?'current':'';
          Object.assign(mark.style,{left:left+'px',top:top+'px',width:width+'px',height:height+'px'});overlay.append(mark);
        }
      }
    }
  }

  function update(scroll=false) {
    previous.disabled=next.disabled=!hits.length;
    count.textContent=!input.value?'输入内容':hits.length?`${current+1} / ${hits.length}`:'无匹配';
    if(scroll&&hits[current])reveal(hits[current]);
    paint();
  }

  function reveal(hit) {
    // A paragraph or code block can be taller than the viewport. Scroll to the
    // matched text itself, including inside horizontal formula/code scrollers.
    for(let node=hit.element;node;node=node.parentElement) {
      const style=getComputedStyle(node),box=node.getBoundingClientRect();
      const rect=[...hit.range.getClientRects()].find(r=>r.width&&r.height);
      if(!rect)break;
      const top=box.top+node.clientTop,left=box.left+node.clientLeft;
      if(/auto|scroll/.test(style.overflowY)) {
        if(rect.top<top)node.scrollTop+=rect.top-top;
        else if(rect.bottom>top+node.clientHeight)node.scrollTop+=rect.bottom-top-node.clientHeight;
      }
      if(/auto|scroll/.test(style.overflowX)) {
        if(rect.left<left)node.scrollLeft+=rect.left-left;
        else if(rect.right>left+node.clientWidth)node.scrollLeft+=rect.right-left-node.clientWidth;
      }
      if(node===$('reading'))break;
    }
  }

  function refresh(reset=false,scroll=false) {
    clearTimeout(timer);hits=[];
    if(!bar.hidden&&input.value&&!doc.hidden)for(const group of collect()) {
      const visible=occurrences(group.text);
      for(const match of visible) {
        const range=rangeFor(group,match);
        if(range)hits.push({range,element:range.startContainer.parentElement});
      }
      if(!group.formula||visible.length)continue;
      const units=[...group.formula.querySelectorAll('[data-formula-tex]')];
      if(group.formula.hasAttribute('data-formula-tex'))units.unshift(group.formula);
      const unitHits=units.filter(n=>occurrences(n.dataset.formulaTex).length);
      const raw=group.formula.querySelector('annotation')?.textContent||'';
      for(const element of unitHits.length?unitHits:occurrences(raw).length?[group.formula]:[]) {
        const range=new Range();range.selectNodeContents(element);hits.push({range,element});
      }
    }
    current=hits.length?(reset?0:Math.max(0,Math.min(current,hits.length-1))):-1;
    update(scroll);
  }

  function open() {
    FormulaEditor.close();callbacks.openView?.();
    bar.hidden=false;toggle.setAttribute('aria-expanded','true');
    refresh(false,true);callbacks.resize?.();input.focus();input.select();
  }
  function close(focus=true) {
    bar.hidden=true;toggle.setAttribute('aria-expanded','false');clearTimeout(timer);hits=[];current=-1;clearPaint();
    if(focus)toggle.focus({preventScroll:true});callbacks.resize?.();
  }
  function move(delta) {
    if(!hits.length)return;
    current=(current+delta+hits.length)%hits.length;update(true);
  }
  input.addEventListener('input',()=>refresh(true,true));
  for(const id of ['find-case','find-word'])$(id).addEventListener('change',()=>refresh(true,true));
  toggle.addEventListener('click',()=>bar.hidden?open():close());
  $('find-close').addEventListener('click',()=>close());
  previous.addEventListener('click',()=>move(-1));next.addEventListener('click',()=>move(1));
  document.addEventListener('keydown',ev=>{
    if(ev.isComposing||ev.keyCode===229)return;
    if((ev.ctrlKey||ev.metaKey)&&!ev.altKey&&!ev.shiftKey&&ev.key.toLowerCase()==='f') {
      ev.preventDefault();ev.stopImmediatePropagation();open();
    } else if(!bar.hidden&&!bar.inert&&ev.key==='Escape') {
      ev.preventDefault();ev.stopImmediatePropagation();close();
    } else if(!bar.hidden&&!bar.inert&&bar.contains(ev.target)&&!ev.target.closest('button')&&ev.key==='Enter'&&!ev.ctrlKey&&!ev.altKey&&!ev.metaKey) {
      ev.preventDefault();ev.stopImmediatePropagation();move(ev.shiftKey?-1:1);
    }
  },true);
  new MutationObserver(()=>{
    if(!bar.hidden){clearPaint();clearTimeout(timer);timer=setTimeout(()=>refresh(),60);}
  }).observe(doc,{childList:true,characterData:true,subtree:true});
  document.addEventListener('scroll',()=>{if(!nativeHighlights&&!bar.hidden)paint();},true);
  new ResizeObserver(()=>{if(!bar.hidden)paint();}).observe($('reading'));
  window.DocumentSearch={init(options){callbacks=options;},close};
})();
