/* Safe variable units. Structural operators and their limits stay untouched. */
(() => {
  const styles = ['sup','sub','both','sup-sub','sub-sup','nested'];
  const names = {'sup':'纯上标','sub':'纯下标','both':'上标与下标','sup-sub':'上标中带下标','sub-sup':'下标中带上标','nested':'组合与嵌套','plain':'原始平排'};
  const greek = new Set('alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega'.split(' '));
  const fonts = new Set('mathcal mathbf mathit mathrm mathsf mathtt mathbb mathfrak boldsymbol vec bar hat tilde overline underline'.split(' '));
  const operators = new Set('sum prod coprod int iint iiint oint bigcup bigcap bigvee bigwedge lim limsup liminf min max inf sup argmin argmax log ln sin cos tan exp det gcd'.split(' '));
  const space=(s,i,end=s.length)=>{while(i<end&&/\s/.test(s[i]))i++;return i;};
  function atomEnd(s,i,end=s.length) {
    i=space(s,i,end);
    if(i>=end)return end;
    if(s[i]==='{') {
      let depth=1,j=i+1;
      for(;j<end&&depth;j++) {
        if(s[j]==='\\'){const command=s.slice(j).match(/^\\(?:[A-Za-z]+|.)/);if(command)j+=command[0].length-1;}
        else if(s[j]==='{')depth++;
        else if(s[j]==='}')depth--;
      }
      return depth?end:j;
    }
    const command=s.slice(i).match(/^\\(?:[A-Za-z]+|.)/);
    return command?i+command[0].length:i+String.fromCodePoint(s.codePointAt(i)).length;
  }
  function baseEnd(s,i) {
    const cmd=s.slice(i).match(/^\\([A-Za-z]+)/);
    if(cmd&&fonts.has(cmd[1]))return atomEnd(s,space(s,i+cmd[0].length));
    if(cmd)return greek.has(cmd[1])?i+cmd[0].length:i;
    return /^[A-Za-z0-9\u0370-\u03ff\u{1d400}-\u{1d7ff}]/u.test(s.slice(i))?atomEnd(s,i):i;
  }
  function complexAtoms(tex) {
    // Keep function arguments and punctuation inside a script intact.
    const s=tex.trim(),end=baseEnd(s,0);
    if(!end)return null;
    const out=[s.slice(0,end)];let i=end;
    function split(value) {
      let start=0,braces=0,parens=0;
      for(let j=0;j<value.length;j++) {
        if(value[j]==='\\'){j=atomEnd(value,j)-1;continue;}
        if(value[j]==='{')braces++;if(value[j]==='}')braces--;
        if(value[j]==='('||value[j]==='[')parens++;if(value[j]===')'||value[j]===']')parens--;
        if(!braces&&!parens&&(value[j]==='_'||value[j]==='^')) {
          if(j>start)out.push(value.slice(start,j).trim());
          const a=space(value,j+1),b=atomEnd(value,a);
          split(value[a]==='{'?value.slice(a+1,b-1):value.slice(a,b));
          start=b;j=b-1;
        }
      }
      if(start<value.length)out.push(value.slice(start).trim());
    }
    while((i=space(s,i))<s.length) {
      if(s[i]!=='_'&&s[i]!=='^')return null;
      const a=space(s,i+1),b=atomEnd(s,a);
      if(a===b)return null;
      split(s[a]==='{'?s.slice(a+1,b-1):s.slice(a,b));i=b;
    }
    return out.length>=2&&out.length<=5&&out.every(Boolean)?out:null;
  }
  function tokens(text) {
    const out=[];
    for(let i=0;i<text.length;) {
      const char=String.fromCodePoint(text.codePointAt(i));
      if (/\s/.test(char)) {i+=char.length;continue;}
      if(char==='\\') {
        const cmd=text.slice(i).match(/^\\([A-Za-z]+)/);
        if(!cmd || !greek.has(cmd[1])) return null;
        out.push(cmd[0]);i+=cmd[0].length;
      } else if (/^[A-Za-z0-9\u0370-\u03ff\u{1d400}-\u{1d7ff}]$/u.test(char)) {out.push(char);i+=char.length;}
      else return null;
    }
    return out;
  }
  function unit(tex) {
    // A variable keeps its own scripts. Structural commands cannot be a base.
    const commands=[...tex.matchAll(/\\([A-Za-z]+)/g)];
    const simple=commands.every(cmd=>greek.has(cmd[1]))&&/^[\sA-Za-z0-9\\{}_^\u0370-\u03ff\u{1d400}-\u{1d7ff}]+$/u.test(tex);
    let depth=0;
    for(const c of tex) {if(c==='{')depth++;if(c==='}')depth--;if(depth<0||depth>3)return null;}
    if(depth) return null;
    // Script boundaries also terminate a TeX command: \pi_b is not \pib.
    const flat=tex.replace(/[{}_^]/g,' ');
    const atoms=simple?tokens(flat):complexAtoms(tex);
    if(!atoms || atoms.length<1 || atoms.length>5) return null;
    // A single T or x is editable by hand but has no guessed attachment.
    const base=atoms[0], tail=atoms.slice(1);
    const shape=atoms.map(x=>/^[0-9]$/.test(x)?'n':'v').join('');
    const variants=[];
    const add=(id,value)=>{if(!variants.some(v=>v.tex===value))variants.push({id,tex:value,label:names[id]||'当前写法'});};
    if(!simple)add('original',tex.trim());
    if(tail.length) {
      const all=tail.join(' '), a=tail[0], b=tail.slice(1).join(' ')||tail[0];
      add('sup',`${base}^{${all}}`);add('sub',`${base}_{${all}}`);
      if(tail.length>1) {
      add('both',`${base}^{${a}}_{${b}}`);
      add('sup-sub',`${base}^{${a}_{${b}}}`);
      add('sub-sup',`${base}_{${a}^{${b}}}`);
      const cut=Math.max(1,Math.ceil(tail.length/2));
      const top=tail.slice(0,cut), bottom=tail.slice(cut);
      const nested=(items,fallback)=>items.length>1?`${items[0]}_{${items.slice(1).join(' ')}}`:(items[0]||fallback);
      add('nested',`${base}^{${nested(top,a)}}_{${nested(bottom,b)}}`);
      add('both-reversed',`${base}^{${b}}_{${a}}`);
      add('sup-sub-reversed',`${base}^{${b}_{${a}}}`);
      add('sub-sup-reversed',`${base}_{${b}^{${a}}}`);
      add('nested-reversed',`${base}^{${nested(bottom,b)}}_{${nested(top,a)}}`);
      }
    }
    add('plain',atoms.join(' '));
    let current=variants.find(v=>v.tex===tex.trim());
    if(!current) {add('original',tex.trim());current=variants[variants.length-1];}
    for(const v of variants)if(v.id.endsWith('-reversed'))v.label=(names[v.id.replace('-reversed','')]||'组合写法')+'（调换位置）';
    return {content:atoms.join(' '),structure:shape,variants,current:current.id,styles};
  }
  function scan(tex) {
    const whole=unit(tex);if(whole)return [{start:0,end:tex.length,tex,unit:whole}];
    const result=[];
    const scripts=(i,end)=>{
      for(;;) {
        const j=space(tex,i,end),limit=tex.slice(j).match(/^\\(?:limits|nolimits|displaylimits)(?![A-Za-z])/);
        if(limit){i=j+limit[0].length;continue;}
        if(tex[j]==='_'||tex[j]==='^')i=atomEnd(tex,j+1,end);else return i;
      }
    };
    function walk(start,end,depth=0) {
    if(depth>32)return;
    for(let i=start;i<end;){
      if(tex[i]==='{'){const j=atomEnd(tex,i,end);walk(i+1,j-1,depth+1);i=j;continue;}
      const cmd=tex.slice(i).match(/^\\([A-Za-z]+)/);
      if(cmd&&!greek.has(cmd[1])&&!fonts.has(cmd[1])){
        i+=cmd[0].length;
        const name=cmd[1];
        if(['left','right','middle'].includes(name)){i=atomEnd(tex,i,end);continue;}
        if(name==='begin'||name==='end') {
          const a=space(tex,i,end),b=atomEnd(tex,a,end),env=tex.slice(a+1,b-1);i=b;
          if(name==='begin'&&env==='array')i=atomEnd(tex,i,end);
          continue;
        }
        if(operators.has(name)){i=scripts(i,end);continue;}
        if(['frac','dfrac','tfrac','binom','dbinom','tbinom','sqrt'].includes(name)) {
          i=space(tex,i,end);
          if(name==='sqrt'&&tex[i]==='['){const close=tex.indexOf(']',i);i=close<0?end:close+1;}
          const count=name==='sqrt'?1:2;
          for(let n=0;n<count;n++) {
            const a=space(tex,i,end),b=atomEnd(tex,a,end);
            walk(tex[a]==='{'?a+1:a,tex[a]==='{'?b-1:b,depth+1);i=b;
          }
          i=scripts(i,end);
          continue;
        }
        if(name==='textcolor'){i=atomEnd(tex,i,end);continue;}
        // Text, operator names and unknown command arguments are not variables.
        for(;;) {
          const j=space(tex,i,end);
          if(tex[j]==='{')i=atomEnd(tex,j,end);else break;
        }
        i=scripts(i,end);
        continue;
      }
      if(tex[i]==='\\'&&!cmd){i=atomEnd(tex,i,end);continue;}
      const a=i,base=baseEnd(tex,i);
      if(base===i){i++;continue;}
      i=base;
      // Preserve short copied runs such as mi or 2k1 as one unit.
      if(!cmd)while(i<end&&/^[A-Za-z0-9\u0370-\u03ff\u{1d400}-\u{1d7ff}]/u.test(tex.slice(i)))i=atomEnd(tex,i,end);
      i=scripts(i,end);
      const value=tex.slice(a,i),parsed=unit(value);
      if(parsed)result.push({start:a,end:i,tex:value,unit:parsed});
    }
    }
    walk(0,tex.length);
    return result;
  }
  function groupFragments(source,selection,parts,text) {
    // A short copied stack can be emitted as two formulas. Match the original
    // selected characters before combining their editing boundary.
    const lines=String(selection||'').trim().replace(/\r\n?/g,'\n').split('\n').map(s=>s.trim()).filter(Boolean);
    if(lines.length!==2)return;
    const normalize=s=>s.normalize('NFKC').replace(/\s/g,'');
    const original=lines.map(normalize);
    if([...original[0]].length!==3||[...original[1]].length!==2 ||
        original.some(s=>!/^[A-Za-z0-9\u0370-\u03ff]+$/u.test(s)))return;
    const math=parts.filter(p=>p.type==='math').sort((a,b)=>a.start-b.start);
    if(math.length!==2||parts.some(p=>p.type!=='math')||math.some(p=>p.start<0))return;
    const [a,b]=math;
    if(source.slice(0,a.start).trim()||source.slice(b.end).trim()||
        !/^\s+$/.test(source.slice(a.end,b.start)))return;
    const first=unit(a.tex),second=unit(b.tex);
    if(!first||!second||normalize(first.content)!==original[0]||normalize(second.content)!==original[1])return;
    const combined=a.tex+' '+b.tex;
    if(!unit(combined))return;
    const expected=`@@KX${a.index}@@`+source.slice(a.end,b.start)+`@@KX${b.index}@@`;
    // Protection normalizes line endings before producing placeholder text.
    const normalizedExpected=expected.replace(/\r\n?/g,'\n');
    if(!text.includes(normalizedExpected))return;
    a.tex=combined;a.raw=source.slice(a.start,b.end);a.end=b.end;
    a.display=a.display||b.display;a.grouped=true;
    b.type='joined';
    return text.replace(normalizedExpected,`@@KX${a.index}@@`);
  }
  window.FormulaUnits={unit,scan,styles,names,groupFragments};
})();
