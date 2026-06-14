// entry.js — pptx-renderer 打包入口
import { PptxViewer, RECOMMENDED_ZIP_LIMITS } from '@aiden0z/pptx-renderer';

// 暴露到全局
window._PptxViewer = PptxViewer;
window._RECOMMENDED_ZIP_LIMITS = RECOMMENDED_ZIP_LIMITS;

console.log('[pptx-renderer-bundle] loaded, version:', PptxViewer.version || 'unknown');
