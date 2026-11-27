import { parseEsp32MapFile } from './src/parser/esp32Parser';
import fs from 'fs';
const content = fs.readFileSync('test(1).map', 'utf-8');
const result = parseEsp32MapFile(content);
console.log('grandTotals:', JSON.stringify(result.grandTotals, null, 2));
console.log('memoryRegions:', JSON.stringify(result.memoryRegions, null, 2));
console.log('executionRegions count:', result.loadRegions[0].executionRegions.length);
for (const r of result.loadRegions[0].executionRegions) {
  console.log(`  ${r.name}  base=0x${r.execBase.toString(16)}  size=${r.size}`);
}
