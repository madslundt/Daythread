// Syntax-aware inventory for localization coverage. Does not inspect user content.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require=createRequire(path.join(root,'src/web/package.json'));
const ts=require('typescript-syntax');
const sourceRoot=path.join(root,'src/web/src');
const exemptions=JSON.parse(fs.readFileSync(path.join(root,'config/localization-exemptions.json'),'utf8')).web;
const textAttributes=new Set(['aria-label','aria-description','placeholder','title','alt','label','subtitle','detail','description','prompt','children','message']);
function human(text){
  return /[A-Za-z]/.test(text)&&(/[A-Za-z] [A-Za-z{]| [a-z]+|[A-Za-z][.!?…]$/.test(text)||/\b[A-Z][a-z]{2,}\b/.test(text));
}
export function inventory(){
 const all=[];
 for(const filename of fs.readdirSync(sourceRoot).filter(f=>/\.tsx?$/.test(f)&&!f.includes('.test.')&&!f.endsWith('.d.ts')&&f!=='i18n.ts')){
  const file=path.join(sourceRoot,filename),source=fs.readFileSync(file,'utf8');
  const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,filename.endsWith('tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
  function visit(node){
   if(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node)||ts.isTemplateExpression(node)||ts.isJsxText(node)){
    const expressions=[];let key='';
    if(ts.isTemplateExpression(node)){
     key=node.head.text;for(const span of node.templateSpans){key+=`{value${expressions.length}}`+span.literal.text;expressions.push(span.expression);}
    }else key=ts.isJsxText(node)?node.text.replace(/\s+/g,' ').trim():node.text;
    const parent=node.parent;
    let ignored=ts.isImportDeclaration(parent)||ts.isExportDeclaration(parent)||ts.isLiteralTypeNode(parent)
      ||(ts.isPropertyAssignment(parent)&&parent.name===node)||(ts.isPropertySignature(parent)&&parent.name===node);
    let localized=false,sourceOnly=false,moduleConstant=false,current=node,attributeResolved=false,functionBoundary=false;
    const ancestry=[];
    while(current.parent){
     const ancestor=current.parent;
     ancestry.push(ts.SyntaxKind[ancestor.kind]+':'+ancestor.getText(ast).slice(0,100));
     if(ts.isCallExpression(ancestor)&&['t','source','countText'].includes(ancestor.expression.getText(ast))&&ancestor.arguments[0]===current){localized=true;sourceOnly=ancestor.expression.getText(ast)==='source';}
     if(!attributeResolved&&ts.isJsxAttribute(ancestor)){attributeResolved=true;if(!textAttributes.has(ancestor.name.getText(ast))&&!ancestor.name.getText(ast).startsWith('on'))ignored=true;}
     if(ts.isJsxElement(ancestor)||ts.isJsxFragment(ancestor)||ts.isJsxSelfClosingElement(ancestor))attributeResolved=true;
     if(ts.isFunctionLike(ancestor))functionBoundary=true;
     if(ts.isVariableStatement(ancestor)&&ts.isSourceFile(ancestor.parent)&&!functionBoundary)moduleConstant=true;
     current=ancestor;
    }
    if(ts.isBinaryExpression(parent)&&[ts.SyntaxKind.EqualsEqualsEqualsToken,ts.SyntaxKind.ExclamationEqualsEqualsToken,ts.SyntaxKind.EqualsEqualsToken,ts.SyntaxKind.ExclamationEqualsToken].includes(parent.operatorToken.kind))ignored=true;
    if(ts.isCallExpression(parent)&&ts.isPropertyAccessExpression(parent.expression)&&['matchMedia','querySelector','querySelectorAll','setAttribute','addEventListener','removeEventListener','getElementById'].includes(parent.expression.name.text))ignored=true;
    const relative=path.relative(root,file);
    if(exemptions[key]||exemptions[relative+'::'+key])ignored=true;
    if((human(key)||(ts.isJsxText(node)&&/[A-Za-z]/.test(key)))&&!ignored)all.push({file:relative,key,start:node.getStart(ast),end:node.getEnd(),line:ast.getLineAndCharacterOfPosition(node.getStart(ast)).line+1,localized,sourceOnly,moduleConstant,kind:ts.SyntaxKind[node.kind],raw:node.getText(ast),expressions:expressions.map(e=>({start:e.getStart(ast),end:e.getEnd(),text:e.getText(ast)})),attribute:ts.isJsxAttribute(parent),ancestry:ancestry.slice(0,3)});
   }
   ts.forEachChild(node,visit);
  }
  visit(ast);
 }
 return all;
}
if(process.argv.includes('--inventory'))console.log(JSON.stringify(inventory(),null,2));
else {
 const missing=inventory().filter(entry=>!entry.localized);
 for(const entry of missing)console.error(`${entry.file}:${entry.line}: unlocalized ${JSON.stringify(entry.key)}`);
 if(missing.length){console.error(`${missing.length} interface strings bypass localization`);process.exitCode=1;}
 else console.log('Web source localization coverage passed.');
}
