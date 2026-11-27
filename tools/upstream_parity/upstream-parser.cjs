"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// ../../third_party/Map-View/src/parser/index.ts
var index_exports = {};
__export(index_exports, {
  parseMapFile: () => parseMapFile
});
module.exports = __toCommonJS(index_exports);

// ../../third_party/Map-View/src/parser/esp32Parser.ts
var MEMORY_REGION_RE = /^(\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s*(\S*)\s*$/i;
var OUTPUT_SECTION_RE = /^(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)(?:\s+load address\s+0x[\da-f]+)?\s*$/;
var ENTRY_RE = /^\s+(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(.+)$/;
var ENTRY_CONTINUED_RE = /^\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(.+)$/;
var SYMBOL_RE = /^\s+(0x[\da-f]+)\s+(\S+)$/;
var FILL_RE = /^\s+\*fill\*\s+(0x[\da-f]+)\s+(0x[\da-f]+)/;
function isLinkerSymbol(name) {
  return /^__(.+_(start|end|size|base|limit)|[A-Z_]+_)$/.test(name) || /^_(end|start|etext|edata|sbss|ebss|sdata)$/.test(name);
}
function parseEsp32MapFile(content) {
  const lines = content.split(/\r?\n/);
  const memoryRegions = [];
  const outputSections = [];
  let phase = "scan";
  let currentSection = null;
  let lastEntry = null;
  let pendingSubSectionName = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("Memory Configuration")) {
      phase = "memconfig";
      i++;
      i++;
      continue;
    }
    if (line.startsWith("Linker script and memory map")) {
      phase = "linkermap";
      continue;
    }
    if (phase === "memconfig") {
      if (line.trim() === "") {
        phase = "scan";
        continue;
      }
      const m = line.match(MEMORY_REGION_RE);
      if (m && m[1] !== "*default*") {
        memoryRegions.push({
          name: m[1],
          origin: parseInt(m[2], 16),
          length: parseInt(m[3], 16),
          attrs: m[4]
        });
      }
    }
    if (phase === "linkermap") {
      if (line.startsWith("LOAD ") || line.includes(" = ")) continue;
      const secMatch = line.match(OUTPUT_SECTION_RE);
      if (secMatch) {
        if (currentSection && currentSection.entries.length > 0) {
          outputSections.push(currentSection);
        }
        currentSection = {
          name: secMatch[1],
          address: parseInt(secMatch[2], 16),
          totalSize: parseInt(secMatch[3], 16),
          entries: []
        };
        lastEntry = null;
        continue;
      }
      if (line.match(/^(\.\S+)\s*$/) && currentSection === null) {
        const nextLine = lines[i + 1] || "";
        const contMatch = nextLine.match(/^\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s*$/);
        if (contMatch) {
          currentSection = {
            name: line.trim(),
            address: parseInt(contMatch[1], 16),
            totalSize: parseInt(contMatch[2], 16),
            entries: []
          };
          lastEntry = null;
          i++;
          continue;
        }
      }
      if (!currentSection) continue;
      const fillMatch = line.match(FILL_RE);
      if (fillMatch) {
        const size = parseInt(fillMatch[2], 16);
        if (size > 0) {
          lastEntry = {
            sectionName: "*fill*",
            address: parseInt(fillMatch[1], 16),
            size,
            objectFile: "*fill*"
          };
          currentSection.entries.push(lastEntry);
        }
        pendingSubSectionName = null;
        continue;
      }
      const entryMatch = line.match(ENTRY_RE);
      if (entryMatch) {
        const size = parseInt(entryMatch[3], 16);
        if (size > 0) {
          lastEntry = {
            sectionName: entryMatch[1],
            address: parseInt(entryMatch[2], 16),
            size,
            objectFile: entryMatch[4].trim()
          };
          currentSection.entries.push(lastEntry);
        }
        pendingSubSectionName = null;
        continue;
      }
      const subSecOnly = line.match(/^\s+(\.\S+)\s*$/);
      if (subSecOnly) {
        pendingSubSectionName = subSecOnly[1];
        continue;
      }
      const contEntryMatch = line.match(ENTRY_CONTINUED_RE);
      if (contEntryMatch) {
        const size = parseInt(contEntryMatch[2], 16);
        const objFile = contEntryMatch[3].trim();
        if (size > 0 && !objFile.startsWith("0x") && !objFile.includes("(size before")) {
          lastEntry = {
            sectionName: pendingSubSectionName || "",
            address: parseInt(contEntryMatch[1], 16),
            size,
            objectFile: objFile
          };
          currentSection.entries.push(lastEntry);
        }
        pendingSubSectionName = null;
        continue;
      }
      const symMatch = line.match(SYMBOL_RE);
      if (symMatch && lastEntry && !symMatch[2].startsWith(".")) {
        const sym = symMatch[2];
        if (!lastEntry.functionName && !isLinkerSymbol(sym)) {
          lastEntry.functionName = sym;
        }
      }
      if (line.match(/^\.\S+/) && !line.match(/^\s/)) {
        if (currentSection && currentSection.entries.length > 0) {
          outputSections.push(currentSection);
        }
        const inlineMatch = line.match(/^(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)/);
        if (inlineMatch) {
          currentSection = {
            name: inlineMatch[1],
            address: parseInt(inlineMatch[2], 16),
            totalSize: parseInt(inlineMatch[3], 16),
            entries: []
          };
        } else {
          currentSection = null;
        }
        lastEntry = null;
      }
    }
  }
  if (currentSection && currentSection.entries.length > 0) {
    outputSections.push(currentSection);
  }
  return buildMapFileData(outputSections, memoryRegions);
}
function classifySectionType(sectionName, parentSection) {
  const combined = parentSection + " " + sectionName;
  if (/\.bss|\.noinit|zidata/i.test(combined)) {
    return { type: "Zero", attr: "RW" };
  }
  if (/\.data/i.test(combined) && !/\.rodata/i.test(combined)) {
    return { type: "Data", attr: "RW" };
  }
  if (/\.rodata|drom/i.test(combined)) {
    return { type: "Data", attr: "RO" };
  }
  if (/\.text|\.literal|iram|\.vectors/i.test(combined)) {
    return { type: "Code", attr: "RO" };
  }
  if (sectionName === "*fill*") {
    return { type: "PAD", attr: "RO" };
  }
  if (/iram|flash\.text/i.test(parentSection)) {
    return { type: "Code", attr: "RO" };
  }
  return { type: "Data", attr: "RO" };
}
function extractObjectName(objPath) {
  const normalized = objPath.replace(/\\/g, "/");
  const libMatch = normalized.match(/([^/]+\.a\(.+?\))/);
  if (libMatch) return libMatch[1];
  const objMatch = normalized.match(/([^/]+\.obj)$/);
  if (objMatch) return objMatch[1];
  const oMatch = normalized.match(/([^/]+\.o)$/);
  if (oMatch) return oMatch[1];
  return objPath;
}
function extractLibraryName(objPath) {
  const normalized = objPath.replace(/\\/g, "/");
  const m = normalized.match(/([^/]+\.a)\(/);
  return m ? m[1] : void 0;
}
function getFunctionFromSection(sectionName) {
  const m = sectionName.match(/\.(?:text|literal|iram\d*)\.(.+)/);
  return m ? m[1] : void 0;
}
function buildMapFileData(outputSections, memoryRegions) {
  const runtimeSections = outputSections.filter((sec) => {
    const name = sec.name.toLowerCase();
    if (name.startsWith(".debug")) return false;
    if (name.startsWith(".comment")) return false;
    if (name.startsWith(".xtensa")) return false;
    if (name.startsWith(".xt.")) return false;
    if (name === ".noload") return false;
    if (name.includes("dummy")) return false;
    if (name === ".ext_ram.dummy") return false;
    return true;
  });
  const loadRegion = {
    name: "ESP32_FLASH",
    baseAddr: 0,
    size: 0,
    maxSize: 0,
    executionRegions: []
  };
  const compMap = /* @__PURE__ */ new Map();
  for (const sec of runtimeSections) {
    if (sec.totalSize === 0) continue;
    const execRegion = {
      name: sec.name,
      execBase: sec.address,
      loadBase: sec.address,
      size: sec.totalSize,
      maxSize: sec.totalSize,
      sections: []
    };
    for (const entry of sec.entries) {
      const { type, attr } = classifySectionType(entry.sectionName, sec.name);
      const funcName = entry.functionName || getFunctionFromSection(entry.sectionName);
      const objName = extractObjectName(entry.objectFile);
      const memSection = {
        execAddr: entry.address,
        loadAddr: null,
        size: entry.size,
        type,
        attr,
        sectionName: entry.sectionName,
        objectName: objName,
        functionName: funcName
      };
      execRegion.sections.push(memSection);
      if (objName !== "*fill*") {
        if (!compMap.has(objName)) {
          compMap.set(objName, { code: 0, roData: 0, rwData: 0, ziData: 0, library: extractLibraryName(entry.objectFile) });
        }
        const comp = compMap.get(objName);
        if (type === "Code") comp.code += entry.size;
        else if (type === "Zero") comp.ziData += entry.size;
        else if (attr === "RW") comp.rwData += entry.size;
        else comp.roData += entry.size;
      }
    }
    if (execRegion.sections.length > 0) {
      loadRegion.executionRegions.push(execRegion);
    }
  }
  loadRegion.size = loadRegion.executionRegions.reduce((s, r) => s + r.size, 0);
  const componentSizes = [];
  const libraryMembers = [];
  for (const [objName, sizes] of compMap) {
    const comp = {
      objectName: objName,
      code: sizes.code,
      codeIncData: sizes.code,
      roData: sizes.roData,
      rwData: sizes.rwData,
      ziData: sizes.ziData,
      debug: 0,
      library: sizes.library
    };
    if (sizes.library) {
      libraryMembers.push(comp);
    } else {
      componentSizes.push(comp);
    }
  }
  let totalCode = 0, totalRoData = 0, totalRwData = 0, totalZiData = 0;
  for (const comp of [...componentSizes, ...libraryMembers]) {
    totalCode += comp.code;
    totalRoData += comp.roData;
    totalRwData += comp.rwData;
    totalZiData += comp.ziData;
  }
  const totalROM = totalCode + totalRoData + totalRwData;
  const totalRW = totalRwData + totalZiData;
  return {
    compiler: "GNU ld (ESP-IDF)",
    compilerVersion: "AC6",
    // reuse AC6 for function name extraction compatibility
    loadRegions: [loadRegion],
    componentSizes,
    libraryMembers,
    librarySizes: [],
    grandTotals: {
      code: totalCode,
      roData: totalRoData,
      rwData: totalRwData,
      ziData: totalZiData,
      totalRO: totalCode + totalRoData,
      totalRW,
      totalROM
    },
    memoryRegions
  };
}

// ../../third_party/Map-View/src/parser/index.ts
var LOAD_REGION_RE = /Load Region (\S+) \(Base: (0x[\da-f]+), Size: (0x[\da-f]+), Max: (0x[\da-f]+)/i;
var EXEC_REGION_RE = /Execution Region (\S+) \(Exec base: (0x[\da-f]+), Load base: (0x[\da-f]+), Size: (0x[\da-f]+), Max: (0x[\da-f]+)/i;
var EIDE_EXEC_REGION_RE = /Execution Region (\S+) \(Base: (0x[\da-f]+), Size: (0x[\da-f]+), Max: (0x[\da-f]+)/i;
var MEMORY_LINE_RE = /^\s*(0x[\da-f]+)\s+(0x[\da-f]+|-|COMPRESSED)\s+(0x[\da-f]+)\s+(Code|Data|Zero|PAD)\s+(RO|RW)\s+(\d+)\s+(\*?)\s*(.+?)\s{2,}(\S+)\s*$/i;
var EIDE_MEMORY_LINE_RE = /^\s*(0x[\da-f]+)\s+(0x[\da-f]+)\s+(Code|Data|Zero|PAD)\s+(RO|RW)\s+(\d+)\s+(\*?)\s*(.+?)\s{2,}(\S+)\s*$/i;
var PAD_LINE_RE = /^\s*(0x[\da-f]+)\s+(0x[\da-f]+|-|COMPRESSED)\s+(0x[\da-f]+)\s+PAD\s*$/i;
var EIDE_PAD_LINE_RE = /^\s*(0x[\da-f]+)\s+(0x[\da-f]+)\s+PAD\s*$/i;
var COMPONENT_LINE_RE = /^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$/;
var TOTALS_LINE_RE = /Total RO\s+Size.*?(\d+)\s*\(/;
var TOTALS_RW_RE = /Total RW\s+Size.*?(\d+)\s*\(/;
var TOTALS_ROM_RE = /Total ROM\s+Size.*?(\d+)\s*\(/;
function detectCompilerVersion(firstLine) {
  if (firstLine.includes("Arm Compiler for Embedded") || /Compiler.*6\.\d/.test(firstLine)) {
    return "AC6";
  }
  return "AC5";
}
function extractFunctionName(sectionName, version) {
  if (version === "AC5") {
    const m = sectionName.match(/^i\.(.+)$/);
    return m ? m[1] : void 0;
  } else {
    const m = sectionName.match(/^\.text\.(.+)$/);
    if (m && !m[1].startsWith("OUTLINED_FUNCTION_")) {
      return m[1];
    }
    return void 0;
  }
}
function parseMapFile(content) {
  if (isGnuLdMap(content)) {
    return parseEsp32MapFile(content);
  }
  const lines = content.split(/\r?\n/);
  const compilerVersion = detectCompilerVersion(lines[0] || "");
  const result = {
    compiler: lines[0] || "",
    compilerVersion,
    loadRegions: [],
    componentSizes: [],
    libraryMembers: [],
    librarySizes: [],
    grandTotals: { code: 0, roData: 0, rwData: 0, ziData: 0, totalRO: 0, totalRW: 0, totalROM: 0 }
  };
  let state = 0 /* Initial */;
  let componentSubState = 0 /* ObjectNames */;
  let currentLoadRegion = null;
  let currentExecRegion = null;
  for (const line of lines) {
    if (line.includes("Section Cross References")) {
      state = 1 /* CrossReferences */;
      continue;
    }
    if (line.includes("Removing Unused input sections")) {
      state = 2 /* RemovedSections */;
      continue;
    }
    if (line.includes("Image Symbol Table")) {
      state = 3 /* SymbolTable */;
      continue;
    }
    if (line.includes("Memory Map of the image")) {
      state = 4 /* MemoryMap */;
      continue;
    }
    if (line.includes("Image component sizes")) {
      state = 5 /* ComponentSizes */;
      componentSubState = 0 /* ObjectNames */;
      continue;
    }
    switch (state) {
      case 4 /* MemoryMap */: {
        const loadMatch = line.match(LOAD_REGION_RE);
        if (loadMatch) {
          currentLoadRegion = {
            name: loadMatch[1],
            baseAddr: parseInt(loadMatch[2], 16),
            size: parseInt(loadMatch[3], 16),
            maxSize: parseInt(loadMatch[4], 16),
            executionRegions: []
          };
          result.loadRegions.push(currentLoadRegion);
          break;
        }
        const execMatch = line.match(EXEC_REGION_RE);
        const eideExecMatch = execMatch ? null : line.match(EIDE_EXEC_REGION_RE);
        if (execMatch || eideExecMatch) {
          const name = (execMatch || eideExecMatch)[1];
          const execBase = parseInt((execMatch || eideExecMatch)[2], 16);
          const loadBase = execMatch ? parseInt(execMatch[3], 16) : execBase;
          const size = parseInt(execMatch ? execMatch[4] : eideExecMatch[3], 16);
          const maxSize = parseInt(execMatch ? execMatch[5] : eideExecMatch[4], 16);
          currentExecRegion = {
            name,
            execBase,
            loadBase,
            size,
            maxSize,
            sections: []
          };
          currentLoadRegion?.executionRegions.push(currentExecRegion);
          break;
        }
        const dataMatch = line.match(MEMORY_LINE_RE);
        const eideDataMatch = dataMatch ? null : line.match(EIDE_MEMORY_LINE_RE);
        if (dataMatch || eideDataMatch) {
          const match = dataMatch || eideDataMatch;
          const sectionName = (dataMatch ? match[8] : match[7]).trim();
          const section = {
            execAddr: parseInt(match[1], 16),
            loadAddr: dataMatch ? match[2] === "-" || match[2] === "COMPRESSED" ? null : parseInt(match[2], 16) : null,
            size: parseInt(dataMatch ? match[3] : match[2], 16),
            type: dataMatch ? match[4] : match[3],
            attr: dataMatch ? match[5] : match[4],
            sectionName,
            objectName: dataMatch ? match[9] : match[8],
            functionName: extractFunctionName(sectionName, compilerVersion),
            isOutlinedFunction: /^\.text\.OUTLINED_FUNCTION_\d+$/.test(sectionName)
          };
          currentExecRegion?.sections.push(section);
          break;
        }
        const padMatch = line.match(PAD_LINE_RE);
        const eidePadMatch = padMatch ? null : line.match(EIDE_PAD_LINE_RE);
        if (padMatch || eidePadMatch) {
          const match = padMatch || eidePadMatch;
          const section = {
            execAddr: parseInt(match[1], 16),
            loadAddr: padMatch ? match[2] === "-" || match[2] === "COMPRESSED" ? null : parseInt(match[2], 16) : null,
            size: parseInt(padMatch ? match[3] : match[2], 16),
            type: "PAD",
            attr: "RO",
            sectionName: "PAD",
            objectName: "[PAD]"
          };
          currentExecRegion?.sections.push(section);
        }
        break;
      }
      case 5 /* ComponentSizes */: {
        if (line.includes("Library Member Name")) {
          componentSubState = 1 /* LibraryMembers */;
          break;
        }
        if (line.includes("Library Name") && !line.includes("Library Member")) {
          componentSubState = 2 /* LibraryNames */;
          break;
        }
        const roMatch = line.match(TOTALS_LINE_RE);
        if (roMatch) {
          result.grandTotals.totalRO = parseInt(roMatch[1]);
          break;
        }
        const rwMatch = line.match(TOTALS_RW_RE);
        if (rwMatch) {
          result.grandTotals.totalRW = parseInt(rwMatch[1]);
          break;
        }
        const romMatch = line.match(TOTALS_ROM_RE);
        if (romMatch) {
          result.grandTotals.totalROM = parseInt(romMatch[1]);
          break;
        }
        if (line.includes("Grand Totals")) {
          const m = line.match(/^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/);
          if (m) {
            result.grandTotals.code = parseInt(m[1]);
            result.grandTotals.roData = parseInt(m[3]);
            result.grandTotals.rwData = parseInt(m[4]);
            result.grandTotals.ziData = parseInt(m[5]);
          }
          componentSubState = 3 /* GrandTotals */;
          break;
        }
        if (line.trim() === "" || line.includes("---") || line.includes("Code (inc. data)") || line.includes("Object Totals") || line.includes("(incl.") || line.includes("Library Totals") || line.includes("ELF Image Totals") || line.includes("ROM Totals")) {
          break;
        }
        const compMatch = line.match(COMPONENT_LINE_RE);
        if (compMatch) {
          const rawName = compMatch[7].trim();
          const libMatch = rawName.match(/^(.+?)\((.+?)\)$/);
          const component = {
            objectName: libMatch ? libMatch[2] : rawName,
            code: parseInt(compMatch[1]),
            codeIncData: parseInt(compMatch[2]),
            roData: parseInt(compMatch[3]),
            rwData: parseInt(compMatch[4]),
            ziData: parseInt(compMatch[5]),
            debug: parseInt(compMatch[6]),
            library: libMatch ? libMatch[1] : void 0,
            member: libMatch ? libMatch[2] : void 0
          };
          switch (componentSubState) {
            case 0 /* ObjectNames */:
              result.componentSizes.push(component);
              break;
            case 1 /* LibraryMembers */:
              result.libraryMembers.push(component);
              break;
            case 2 /* LibraryNames */:
              result.librarySizes.push(component);
              break;
          }
        }
        break;
      }
    }
  }
  if (result.grandTotals.totalRO === 0 && result.grandTotals.code > 0) {
    result.grandTotals.totalRO = result.grandTotals.code + result.grandTotals.roData;
  }
  if (result.grandTotals.totalRW === 0 && (result.grandTotals.rwData > 0 || result.grandTotals.ziData > 0)) {
    result.grandTotals.totalRW = result.grandTotals.rwData + result.grandTotals.ziData;
  }
  if (result.grandTotals.totalROM === 0 && result.grandTotals.totalRO > 0) {
    result.grandTotals.totalROM = result.grandTotals.totalRO + result.grandTotals.rwData;
  }
  return result;
}
function isGnuLdMap(content) {
  const firstLine = content.slice(0, content.indexOf("\n"));
  if (firstLine.includes("Arm Compiler") || firstLine.includes("Component:")) {
    return false;
  }
  const head = content.slice(0, 200);
  if (head.includes("Archive member included") || head.startsWith("LOAD ")) {
    return true;
  }
  return content.includes("Memory Configuration") && content.includes("Linker script and memory map");
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  parseMapFile
});
