/* Common LaTeX prose mapped to the existing Markdown renderer. No TeX execution. */
(() => {
  const environments='itemize|enumerate|description';
  const command=new RegExp('^\\\\(begin|end)\\{('+environments+')\\}|^\\\\(item)(?![A-Za-z])');
  function group(source,start,open='{',close='}') {
    if(source[start]!==open)return null;
    let depth=1;
    for(let i=start+1;i<source.length;i++){
      if(source[i]==='\\'){i++;continue;}
      if(source[i]===open)depth++;
      if(source[i]===close&&!--depth)return {value:source.slice(start+1,i),end:i+1};
    }
    return null;
  }
  function next(source,start) {
    let braces=0;
    for(let i=start;i<source.length;i++){
      if(source[i]==='\\'){
        const token=!braces&&source.slice(i).match(command);
        if(token)return {kind:token[1]||token[3],env:token[2],start:i,end:i+token[0].length};
        if(!/[A-Za-z]/.test(source[i+1]||''))i++;
      }else if(source[i]==='{')braces++;
      else if(source[i]==='}')braces=Math.max(0,braces-1);
    }
    return null;
  }
  const clean=text=>text.replace(/\n[ \t]+/g,'\n');
  function list(source,begin,depth=0) {
    if(depth>16)return null;
    const items=[];let current=null,prefix='',cursor=begin.end;
    // Layout options such as [noitemsep] do not form part of the item prose.
    const optionStart=cursor+(source.slice(cursor).match(/^\s*/)||[''])[0].length;
    const option=group(source,optionStart,'[',']');if(option)cursor=option.end;
    const append=text=>{if(current)current.text+=text;else prefix+=text;};
    for(;;){
      const token=next(source,cursor);if(!token)return null;
      append(clean(source.slice(cursor,token.start)));
      if(token.kind==='begin'){
        const child=list(source,token,depth+1);if(!child)return null;
        append('\n\n'+child.text+'\n\n');cursor=child.end;continue;
      }
      if(token.kind==='end'){
        if(token.env!==begin.env||!items.length)return null;
        const text=items.map((item,index)=>{
          const marker=begin.env==='enumerate'?(index+1)+'. ':'- ';
          const label=item.label?'**'+item.label.trim()+'** ':'';
          const body=(label+item.text.trim()).replace(/\n/g,'\n'+' '.repeat(marker.length));
          return marker+body;
        }).join('\n\n');
        return {text:(prefix.trim()?prefix.trim()+'\n\n':'')+text,end:token.end};
      }
      current={text:'',label:''};items.push(current);cursor=token.end;
      const labelStart=cursor+(source.slice(cursor).match(/^\s*/)||[''])[0].length;
      const label=group(source,labelStart,'[',']');
      if(label){current.label=label.value;cursor=label.end;}
    }
  }
  const letters={aa:'å',AA:'Å',ae:'æ',AE:'Æ',oe:'œ',OE:'Œ',o:'ø',O:'Ø',l:'ł',L:'Ł',ss:'ß',i:'ı',j:'ȷ'};
  const accents={"'":'\u0301','`':'\u0300','"':'\u0308','^':'\u0302','~':'\u0303','=':'\u0304','.':'\u0307',u:'\u0306',v:'\u030c',H:'\u030b',c:'\u0327',k:'\u0328',r:'\u030a',b:'\u0331',d:'\u0323'};
  function prose(source) {
    // Called only after code and explicit math have been protected by the card.
    // Restrict conversion to known text commands. Unknown macros stay visible.
    const authored=/\\(?:begin\{(?:itemize|enumerate|description)\}|(?:section|subsection|subsubsection|textbf|textit|emph|noindent|aa|AA|ae|AE|oe|OE|ss)(?![A-Za-z]))/.test(source);
    const accent=/(?<!\\)\\(['`"^~=.]|[uvHckrbd](?![A-Za-z]))\s*(?:\{(\\[ij](?:\{\})?|[A-Za-z])\}|([A-Za-z]))/g;
    const accented=(_,command,braced,plain)=>((braced||plain).replace(/^\\([ij])(?:\{\})?$/,'$1')+accents[command]).normalize('NFC');
    source=source.replace(new RegExp('\\{'+accent.source+'\\s*\\}','g'),accented).replace(accent,accented);
    source=source.replace(/\{\\(aa|AA|ae|AE|oe|OE|o|O|l|L|ss|i|j)\s*\}/g,(_,name)=>letters[name]);
    source=source.replace(/(?<!\\)\\(aa|AA|ae|AE|oe|OE|o|O|l|L|ss|i|j)(?![A-Za-z])(?:\{\})?/g,(_,name)=>letters[name]);
    if(authored)source=source.replace(/(?<=\S)(---|--)(?=\S)/g,(_,dash)=>dash.length===3?'—':'–');
    source=source.replace(/(?<!\\)\\(noindent|indent|ignorespaces|unskip|relax|centering|raggedright|raggedleft|normalfont|normalcolor|small|footnotesize|scriptsize|tiny|normalsize|large|Large|LARGE|huge|Huge)(?![A-Za-z])/g,'');
    source=source.replace(/(?<!\\)\\(par|newline|linebreak|smallskip|medskip|bigskip|pagebreak|newpage|clearpage)(?![A-Za-z])(?:\[[0-4]\])?/g,'\n\n');
    source=source.replace(/(?<!\\)\\(quad|qquad|enspace|enskip|hfill|vfill)(?![A-Za-z])/g,' ');
    source=source.replace(/(?<!\\)\\(?:hspace|vspace)\*?\s*\{[^{}\n]*\}/g,' ');
    return source;
  }
  const levels=['section','subsection','subsubsection'];
  function roman(value) {
    let result='';
    for(const [number,label] of [[1000,'M'],[900,'CM'],[500,'D'],[400,'CD'],[100,'C'],[90,'XC'],[50,'L'],[40,'XL'],[10,'X'],[9,'IX'],[5,'V'],[4,'IV'],[1,'I']])
      while(value>=number){result+=label;value-=number;}
    return result;
  }
  function alphabet(value) {
    let result='';
    while(value>0){value--;result=String.fromCharCode(65+value%26)+result;value=Math.floor(value/26);}
    return result;
  }
  function formatting(source,context,depth=0) {
    if(depth>16)return source;
    let result='',cursor=0;
    const pattern=/\\(textbf|textit|emph|section|subsection|subsubsection|setcounter)(\*)?\s*\{/g;
    let match;
    while((match=pattern.exec(source))){
      // A doubled backslash is a TeX line break, not a formatting command.
      if(match.index&&source[match.index-1]==='\\')continue;
      const argument=group(source,pattern.lastIndex-1);if(!argument)continue;
      const name=match[1];
      if(name==='setcounter'){
        const index=levels.indexOf(argument.value.trim());
        const start=argument.end+(source.slice(argument.end).match(/^\s*/)||[''])[0].length;
        const count=group(source,start);
        if(index<0||!count||!/^\d{1,4}$/.test(count.value.trim())||Number(count.value)>=3999)continue;
        context.counters[index]=Number(count.value);
        result+=source.slice(cursor,match.index);cursor=count.end;pattern.lastIndex=cursor;continue;
      }
      const value=formatting(argument.value,context,depth+1);
      let replacement;
      if(name==='textbf')replacement='**'+value+'**';
      else if(name==='textit'||name==='emph')replacement='*'+value+'*';
      else {
        const level=levels.indexOf(name);let number='';
        if(!match[2]){
          context.counters[level]++;
          for(let i=level+1;i<3;i++)context.counters[i]=0;
          const count=context.counters[level];
          number=(level===0?roman(count)+'.':level===1?alphabet(count)+'.':count+')')+' ';
        }
        const marker=context.decorate?context.prefix+context.headings.length+'@@':'';
        if(marker)context.headings.push({marker,name});
        const title=number+value;
        replacement='\n\n'+'#'.repeat(level+2)+' '+marker+(level?'*'+title+'*':title)+'\n\n';
      }
      result+=source.slice(cursor,match.index)+replacement;cursor=argument.end;pattern.lastIndex=cursor;
    }
    return result+source.slice(cursor);
  }
  function convert(source,decorate=true) {
    source=prose(source);
    let result='',cursor=0,search=0;
    for(;;){
      const token=next(source,search);if(!token)break;
      search=token.end;if(token.kind!=='begin')continue;
      const parsed=list(source,token);if(!parsed)continue;
      result+=source.slice(cursor,token.start)+'\n\n'+parsed.text+'\n\n';cursor=parsed.end;search=cursor;
    }
    let prefix='@@LTXH';while(source.includes(prefix))prefix+='X';
    const context={counters:[0,0,0],headings:[],prefix,decorate};
    return {text:formatting(result+source.slice(cursor),context),headings:context.headings};
  }
  function decorate(el,headings) {
    // Apply our own classes after Markdown has escaped untrusted HTML. Never
    // trust HTML from the translation to carry presentation metadata.
    for(const heading of el.querySelectorAll('h2,h3,h4')){
      const walker=document.createTreeWalker(heading,NodeFilter.SHOW_TEXT);
      const node=walker.nextNode();if(!node)continue;
      const meta=headings.find(h=>node.data.startsWith(h.marker));if(!meta)continue;
      node.deleteData(0,meta.marker.length);heading.classList.add('latex-'+meta.name);
    }
  }
  window.LatexText={toMarkdown:source=>convert(source,false).text,convert,decorate};
})();
