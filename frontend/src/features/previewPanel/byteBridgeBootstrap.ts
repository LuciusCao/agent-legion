/**
 * 字节桥 bootstrap：宿主注入 bundle 的第一段脚本（#1178 codex 复审 P1
 * 第 4 轮的根因修复——能力发放从「宿主随 init 下发」反转为「初始文档
 * 自证」）。
 *
 * 为什么必须注入而不是随 init 下发 port：窗口通道无法鉴别「初始 srcdoc
 * 文档」与「面板自导航后的文档」（WindowProxy 跨导航同一、opaque origin
 * 的 event.origin 恒 "null"）。宿主每棵挂载只写一次 srcDoc（bundle 内容
 * 变化走 previewHostKey 整树重挂），因此**注入脚本是唯一保证运行在初始
 * 文档里的宿主代码**——它作为 head 第一个脚本在解析期同步执行，先于
 * bundle 任何代码与 meta refresh。bootstrap 自建 MessageChannel：port2
 * 存进闭包（面板只拿到 readArtifactBytes 函数，端口本体不可取出、不可
 * 转交），port1 经 byte-port-offer 消息上交宿主。宿主每个挂载只接受第
 * 一次上交（见 portBridge.ts）——初始文档的 offer 先于任何导航后文档
 * 可能发出的消息入队，排序即鉴别；其后的上交（含攻击者伪造）一律拒绝。
 * 面板自导航销毁旧 global，闭包里的 port2 随之失效——能力由此绑定初始
 * 文档的存活期，而宿主侧永不重新发放（init 重发只带数据、不带端口）。
 *
 * 注意：脚本文本会被序列化进 HTML（panelCsp.ts），不得包含 `</script`
 * 序列；字符串常量一律 JSON.stringify 内联。
 */
import {
  BYTE_PORT_OFFER_TYPE,
  PREVIEW_BYTE_BRIDGE_GLOBAL,
  PREVIEW_PANEL_SOURCE,
} from './bridge'

/**
 * 注入脚本文本。幂等守卫（重复注入/同文档重复执行直接返回）；闭包持有
 * port2，暴露的全局是冻结对象、属性不可写不可配置（防 bundle 无意覆盖；
 * bundle 调用它是合法路径，防的不是它）。
 */
export const BYTE_BRIDGE_BOOTSTRAP = `(function(){
var G=${JSON.stringify(PREVIEW_BYTE_BRIDGE_GLOBAL)};
if(window[G])return;
var channel=new MessageChannel();
var port=channel.port2;
var pending=new Map();
var nextId=0;
port.onmessage=function(event){
var data=event.data;
if(!data||data.type!=='response')return;
var entry=pending.get(data.id);
if(!entry)return;
pending.delete(data.id);
if(data.ok)entry.resolve(data.payload);
else entry.reject(new Error(typeof data.error==='string'?data.error:'bridge error'));
};
if(port.start)port.start();
var bridge={
readArtifactBytes:function(name){
return new Promise(function(resolve,reject){
nextId+=1;
pending.set(nextId,{resolve:resolve,reject:reject});
port.postMessage({type:'request',id:nextId,method:'readArtifactBytes',params:{name:name}});
});
}
};
try{
Object.defineProperty(window,G,{value:Object.freeze(bridge),writable:false,configurable:false});
}catch(e){
window[G]=bridge;
}
window.parent.postMessage({source:${JSON.stringify(PREVIEW_PANEL_SOURCE)},type:${JSON.stringify(BYTE_PORT_OFFER_TYPE)}},'*',[channel.port1]);
})()`
