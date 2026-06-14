import { PptxHandler } from 'pptx-viewer-core';

// 暴露到全局
window.PptxHandler = PptxHandler;

// 测试1：解析现有 .pptx
async function testParse() {
  const handler = new PptxHandler();
  // 从 python 生成的 base64 加载
  const b64 = window.__pptxBase64;
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  
  const data = await handler.load(bytes.buffer);
  console.log('Slides:', data.slides.length);
  console.log('Theme:', data.theme?.name);
  
  // 遍历元素
  for (const slide of data.slides) {
    console.log(`Slide ${slide.id}: ${slide.elements.length} elements`);
    for (const el of slide.elements) {
      console.log('  -', el.type, el.text?.substring(0, 30));
    }
  }
  
  window.__pptxData = data;
  window.__pptxHandler = handler;
  return data;
}

// 测试2：修改并保存
async function testEdit() {
  const { data, handler } = window;
  if (!data) return;
  
  // 修改第一个文本元素
  const firstText = data.slides[0].elements.find(e => e.type === 'text');
  if (firstText) {
    firstText.text = 'Modified by AI: ' + firstText.text;
    console.log('Modified:', firstText.text);
  }
  
  // 保存
  const output = await handler.save(data.slides);
  console.log('Saved bytes:', output.byteLength);
  
  // 转 base64 回传 python
  const outBytes = new Uint8Array(output);
  let b64 = '';
  for (let i = 0; i < outBytes.length; i++) b64 += String.fromCharCode(outBytes[i]);
  window.__pptxOutputBase64 = btoa(b64);
  
  return output;
}

window.testParse = testParse;
window.testEdit = testEdit;
