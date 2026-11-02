// Episode-local lossless compression override for low-disk rendering.
// The shared renderer, frame pixels, timings, audio and final encoding stay intact.
import {createRequire} from 'node:module';
const sharp=createRequire(import.meta.url)('sharp');
const png=sharp.prototype.png;
sharp.prototype.png=function(options={}){
  return png.call(this,{...options,compressionLevel:9});
};
