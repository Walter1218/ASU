// ESM wrapper for jszip (CommonJS)
import JSZipModule from './jszip.min.js';
const JSZip = JSZipModule.default || JSZipModule;
export default JSZip;
export { JSZip };
