/* Compare upstream TypeScript parser statistics with the Python port.
 *
 * Usage: node compare.js <file.map> [...]
 * Requires upstream-parser.cjs (built with esbuild from third_party/Map-View).
 */
'use strict';

const fs = require('fs');
const { parseMapFile } = require('./upstream-parser.cjs');

function summarize(file) {
  const text = fs.readFileSync(file, 'utf8');
  const data = parseMapFile(text);
  const execRegions = data.loadRegions.flatMap((region) => region.executionRegions);
  const sections = execRegions.reduce((sum, region) => sum + region.sections.length, 0);
  return {
    file,
    compilerVersion: data.compilerVersion,
    loadRegions: data.loadRegions.length,
    executionRegions: execRegions.length,
    sections,
    components: data.componentSizes.length,
    libraryMembers: data.libraryMembers.length,
    librarySizes: data.librarySizes.length,
    grandTotals: data.grandTotals,
    memoryRegions: (data.memoryRegions || []).length,
    sectionTypes: execRegions
      .flatMap((region) => region.sections)
      .reduce((counts, section) => {
        counts[section.type] = (counts[section.type] || 0) + 1;
        return counts;
      }, {}),
    functionNames: execRegions
      .flatMap((region) => region.sections)
      .map((section) => section.functionName)
      .filter((name) => name),
  };
}

const files = process.argv.slice(2);
for (const file of files) {
  console.log(JSON.stringify(summarize(file), null, 2));
}
