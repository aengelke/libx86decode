#!/usr/bin/python3

import argparse
from collections import OrderedDict, defaultdict, namedtuple, Counter
from enum import Enum
from itertools import product
import struct
from typing import NamedTuple, FrozenSet, List, Tuple, Union, Optional, ByteString

INSTR_FLAGS_FIELDS, INSTR_FLAGS_SIZES = zip(*[
    ("modrm_idx", 2),
    ("modreg_idx", 2),
    ("vexreg_idx", 2),
    ("zeroreg_idx", 2),
    ("imm_idx", 2),
    ("zeroreg_val", 1),
    ("lock", 1),
    ("imm_control", 3),
    ("vsib", 1),
    ("op0_size", 2),
    ("op1_size", 2),
    ("op2_size", 2),
    ("op3_size", 2),
    ("opsize", 2),
    ("size_fix1", 3),
    ("size_fix2", 2),
    ("instr_width", 1),
    ("op0_regty", 3),
    ("op1_regty", 3),
    ("op2_regty", 3),
    ("unused", 6),
    ("ign66", 1),
][::-1])
class InstrFlags(namedtuple("InstrFlags", INSTR_FLAGS_FIELDS)):
    def __new__(cls, **kwargs):
        init = {**{f: 0 for f in cls._fields}, **kwargs}
        return super(InstrFlags, cls).__new__(cls, **init)
    def _encode(self):
        enc = 0
        for value, size in zip(self, INSTR_FLAGS_SIZES):
            enc = enc << size | (value & ((1 << size) - 1))
        return enc

ENCODINGS = {
    "NP": InstrFlags(),
    "M": InstrFlags(modrm_idx=0^3),
    "M1": InstrFlags(modrm_idx=0^3, imm_idx=1^3, imm_control=1),
    "MI": InstrFlags(modrm_idx=0^3, imm_idx=1^3, imm_control=4),
    "MC": InstrFlags(modrm_idx=0^3, zeroreg_idx=1^3, zeroreg_val=1),
    "MR": InstrFlags(modrm_idx=0^3, modreg_idx=1^3),
    "RM": InstrFlags(modrm_idx=1^3, modreg_idx=0^3),
    "RMA": InstrFlags(modrm_idx=1^3, modreg_idx=0^3, zeroreg_idx=2^3),
    "MRI": InstrFlags(modrm_idx=0^3, modreg_idx=1^3, imm_idx=2^3, imm_control=4),
    "RMI": InstrFlags(modrm_idx=1^3, modreg_idx=0^3, imm_idx=2^3, imm_control=4),
    "MRC": InstrFlags(modrm_idx=0^3, modreg_idx=1^3, zeroreg_idx=2^3, zeroreg_val=1),
    "AM": InstrFlags(modrm_idx=1^3, zeroreg_idx=0^3),
    "MA": InstrFlags(modrm_idx=0^3, zeroreg_idx=1^3),
    "I": InstrFlags(imm_idx=0^3, imm_control=4),
    "IA": InstrFlags(zeroreg_idx=0^3, imm_idx=1^3, imm_control=4),
    "O": InstrFlags(modreg_idx=0^3),
    "OI": InstrFlags(modreg_idx=0^3, imm_idx=1^3, imm_control=4),
    "OA": InstrFlags(modreg_idx=0^3, zeroreg_idx=1^3),
    "S": InstrFlags(modreg_idx=0^3, vsib=1), # segment register in bits 3,4,5
    "A": InstrFlags(zeroreg_idx=0^3),
    "D": InstrFlags(imm_idx=0^3, imm_control=6),
    "FD": InstrFlags(zeroreg_idx=0^3, imm_idx=1^3, imm_control=2),
    "TD": InstrFlags(zeroreg_idx=1^3, imm_idx=0^3, imm_control=2),

    "RVM": InstrFlags(modrm_idx=2^3, modreg_idx=0^3, vexreg_idx=1^3),
    "RVMI": InstrFlags(modrm_idx=2^3, modreg_idx=0^3, vexreg_idx=1^3, imm_idx=3^3, imm_control=4),
    "RVMR": InstrFlags(modrm_idx=2^3, modreg_idx=0^3, vexreg_idx=1^3, imm_idx=3^3, imm_control=3),
    "RMV": InstrFlags(modrm_idx=1^3, modreg_idx=0^3, vexreg_idx=2^3),
    "VM": InstrFlags(modrm_idx=1^3, vexreg_idx=0^3),
    "VMI": InstrFlags(modrm_idx=1^3, vexreg_idx=0^3, imm_idx=2^3, imm_control=4),
    "MVR": InstrFlags(modrm_idx=0^3, modreg_idx=2^3, vexreg_idx=1^3),
}

class OpKind(NamedTuple):
    size: int
    kind: str

    SZ_OP = -1
    SZ_VEC = -2
    K_MEM = "mem"
    K_IMM = "imm"

    def abssize(self, opsz=None, vecsz=None):
        res = opsz if self.size == self.SZ_OP else \
              vecsz if self.size == self.SZ_VEC else self.size
        if res is None:
            raise Exception("unspecified operand size")
        return res

OPKINDS = {
    # sizeidx (0, fixedsz, opsz, vecsz), fixedsz (log2), regtype
    "IMM": OpKind(OpKind.SZ_OP, OpKind.K_IMM),
    "IMM8": OpKind(1, OpKind.K_IMM),
    "IMM16": OpKind(2, OpKind.K_IMM),
    "IMM32": OpKind(4, OpKind.K_IMM),
    "IMM64": OpKind(8, OpKind.K_IMM),
    "GP": OpKind(OpKind.SZ_OP, "GP"),
    "GP8": OpKind(1, "GP"),
    "GP16": OpKind(2, "GP"),
    "GP32": OpKind(4, "GP"),
    "GP64": OpKind(8, "GP"),
    "MMX": OpKind(8, "MMX"),
    "XMM": OpKind(OpKind.SZ_VEC, "XMM"),
    "XMM8": OpKind(1, "XMM"),
    "XMM16": OpKind(2, "XMM"),
    "XMM32": OpKind(4, "XMM"),
    "XMM64": OpKind(8, "XMM"),
    "XMM128": OpKind(16, "XMM"),
    "XMM256": OpKind(32, "XMM"),
    "SEG": OpKind(OpKind.SZ_OP, "SEG"),
    "SEG16": OpKind(2, "SEG"),
    "FPU": OpKind(10, "FPU"),
    "MEM": OpKind(OpKind.SZ_OP, OpKind.K_MEM),
    "MEMV": OpKind(OpKind.SZ_VEC, OpKind.K_MEM),
    "MEMZ": OpKind(0, OpKind.K_MEM),
    "MEM8": OpKind(1, OpKind.K_MEM),
    "MEM16": OpKind(2, OpKind.K_MEM),
    "MEM32": OpKind(4, OpKind.K_MEM),
    "MEM64": OpKind(8, OpKind.K_MEM),
    "MEM128": OpKind(16, OpKind.K_MEM),
    "MEM256": OpKind(32, OpKind.K_MEM),
    "MEM512": OpKind(64, OpKind.K_MEM),
    "MASK8": OpKind(1, "MASK"),
    "MASK16": OpKind(2, "MASK"),
    "MASK32": OpKind(4, "MASK"),
    "MASK64": OpKind(8, "MASK"),
    "BND": OpKind(0, "BND"),
    "CR": OpKind(0, "CR"),
    "DR": OpKind(0, "DR"),
}

class InstrDesc(NamedTuple):
    mnemonic: str
    encoding: str
    operands: Tuple[str, ...]
    flags: FrozenSet[str]

    OPKIND_REGTYS = {"GP": 0, "FPU": 1, "XMM": 2, "MASK": 3, "MMX": 4, "BND": 5}
    OPKIND_SIZES = {
        0: 0, 1: 1, 2: 2, 4: 3, 8: 4, 16: 5, 32: 6, 64: 7, 10: 0,
        OpKind.SZ_OP: -2, OpKind.SZ_VEC: -3,
    }

    @classmethod
    def parse(cls, desc):
        desc = desc.split()
        operands = tuple(OPKINDS[op] for op in desc[1:5] if op != "-")
        return cls(desc[5], desc[0], operands, frozenset(desc[6:]))

    def encode(self, ign66):
        flags = ENCODINGS[self.encoding]
        extraflags = {}

        opsz = set(self.OPKIND_SIZES[opkind.size] for opkind in self.operands)

        # Sort fixed sizes encodable in size_fix2 as second element.
        fixed = sorted((x for x in opsz if x >= 0), key=lambda x: 1 <= x <= 4)
        if len(fixed) > 2 or (len(fixed) == 2 and not (1 <= fixed[1] <= 4)):
            raise Exception("invalid fixed operand sizes: %r"%fixed)
        sizes = (fixed + [1, 1])[:2] + [-2, -3] # See operand_sizes in decode.c.
        extraflags["size_fix1"] = sizes[0]
        extraflags["size_fix2"] = sizes[1] - 1

        for i, opkind in enumerate(self.operands):
            sz = self.OPKIND_SIZES[opkind.size]
            reg_type = self.OPKIND_REGTYS.get(opkind.kind, 7)
            extraflags["op%d_size"%i] = sizes.index(sz)
            if i < 3:
                extraflags["op%d_regty"%i] = reg_type
            elif reg_type not in (7, 2):
                raise Exception("invalid regty for op 3, must be VEC")

        # Miscellaneous Flags
        if "SIZE_8" in self.flags:      extraflags["opsize"] = 1
        if "DEF64" in self.flags:       extraflags["opsize"] = 2
        if "FORCE64" in self.flags:     extraflags["opsize"] = 3
        if "INSTR_WIDTH" in self.flags: extraflags["instr_width"] = 1
        if "LOCK" in self.flags:        extraflags["lock"] = 1
        if "VSIB" in self.flags:        extraflags["vsib"] = 1

        if "USE66" not in self.flags and (ign66 or "IGN66" in self.flags):
            extraflags["ign66"] = 1

        if flags.imm_control >= 4:
            imm_op = next(op for op in self.operands if op.kind == OpKind.K_IMM)
            if ("IMM_8" in self.flags or imm_op.size == 1 or
                (imm_op.size == OpKind.SZ_OP and "SIZE_8" in self.flags)):
                extraflags["imm_control"] = flags.imm_control | 1

        enc = flags._replace(**extraflags)._encode()
        enc = tuple((enc >> i) & 0xffff for i in range(0, 48, 16))
        # First 2 bytes are the mnemonic, last 6 bytes are the encoding.
        return ("FDI_"+self.mnemonic,) + enc

class EntryKind(Enum):
    NONE = 0
    INSTR = 1
    TABLE256 = 2
    TABLE16 = 3
    TABLE8E = 4
    TABLE_PREFIX = 5
    TABLE_VEX = 6
    TABLE_ROOT = -1

class TrieEntry(NamedTuple):
    kind: EntryKind
    items: Tuple[Optional[str]]
    descidx: Optional[int]

    TABLE_LENGTH = {
        EntryKind.TABLE256: 256,
        EntryKind.TABLE16: 16,
        EntryKind.TABLE8E: 8,
        EntryKind.TABLE_PREFIX: 4,
        EntryKind.TABLE_VEX: 4,
        EntryKind.TABLE_ROOT: 8,
    }
    @classmethod
    def table(cls, kind):
        return cls(kind, (None,) * cls.TABLE_LENGTH[kind], ())
    @classmethod
    def instr(cls, descidx):
        return cls(EntryKind.INSTR, (), descidx)

import re
opcode_regex = re.compile(
    r"^(?:(?P<prefixes>(?P<vex>VEX\.)?(?P<legacy>NP|66|F2|F3|NFx)\." +
                     r"(?:W(?P<rexw>[01]|IG)\.)?(?:L(?P<vexl>[01]|IG)\.)?))?" +
     r"(?P<escape>0f38|0f3a|0f|)" +
     r"(?P<opcode>[0-9a-f]{2})" +
     r"(?:(?P<extended>\+)|/(?P<modreg>[0-7]|[rm]|[0-7][rm])|(?P<opcext>[c-f][0-9a-f]))?$")

class Opcode(NamedTuple):
    prefix: Union[None, str] # None/NP/66/F2/F3/NFx
    escape: int # [0, 0f, 0f38, 0f3a]
    opc: int
    extended: bool # Extend opc or opcext, if present
    modreg: Union[None, Tuple[Union[None, int], str]] # (modreg, "r"/"m"/"rm"), None
    opcext: Union[None, int] # 0xc0-0xff, or 0
    vex: bool
    vexl: Union[str, None] # 0, 1, IG, None = used, both
    rexw: Union[str, None] # 0, 1, IG, None = used, both

    @classmethod
    def parse(cls, opcode_string):
        match = opcode_regex.match(opcode_string)
        if match is None:
            raise Exception(opcode_string)
            return None

        modreg = match.group("modreg")
        if modreg:
            if modreg[0] in "rm":
                modreg = None, modreg[0]
            else:
                modreg = int(modreg[0]), modreg[1] if len(modreg) == 2 else "rm"

        return cls(
            prefix=match.group("legacy"),
            escape=["", "0f", "0f38", "0f3a"].index(match.group("escape")),
            opc=int(match.group("opcode"), 16),
            extended=match.group("extended") is not None,
            modreg=modreg,
            opcext=int(match.group("opcext") or "0", 16) or None,
            vex=match.group("vex") is not None,
            vexl=match.group("vexl"),
            rexw=match.group("rexw"),
        )

    def for_trie(self):
        opcode = []
        opcode.append((EntryKind.TABLE_ROOT, [self.escape | self.vex << 2]))
        if not self.extended:
            opcode.append((EntryKind.TABLE256, [self.opc]))
        else:
            opcode.append((EntryKind.TABLE256, [self.opc + i for i in range(8)]))
        if self.prefix:
            if self.prefix == "NFx":
                opcode.append((EntryKind.TABLE_PREFIX, [0, 1]))
            else:
                prefix_val = ["NP", "66", "F3", "F2"].index(self.prefix)
                opcode.append((EntryKind.TABLE_PREFIX, [prefix_val]))
        if self.opcext:
            opcode.append((EntryKind.TABLE16, [((self.opcext - 0xc0) >> 3) | 8]))
            opcode.append((EntryKind.TABLE8E, [self.opcext & 7]))
        if self.modreg:
            # TODO: optimize for /r and /m specifiers to reduce size
            mod = {"m": [0], "r": [1<<3], "rm": [0, 1<<3]}[self.modreg[1]]
            reg = [self.modreg[0]] if self.modreg[0] is not None else list(range(8))
            opcode.append((EntryKind.TABLE16, [x + y for x in mod for y in reg]))
        if self.vexl in ("0", "1") or self.rexw in ("0", "1"):
            rexw = {"0": [0], "1": [1<<0], "IG": [0, 1<<0]}[self.rexw or "IG"]
            vexl = {"0": [0], "1": [1<<1], "IG": [0, 1<<1]}[self.vexl or "IG"]
            entries = list(map(sum, product(rexw, vexl)))
            opcode.append((EntryKind.TABLE_VEX, entries))

        kinds, values = zip(*opcode)
        return [tuple(zip(kinds, prod)) for prod in product(*values)]

def format_opcode(opcode):
    opcode_string = ""
    prefix = ""
    for kind, byte in opcode:
        if kind == EntryKind.TABLE_ROOT:
            opcode_string += ["", "0f", "0f38", "0f3a"][byte & 3]
            prefix += ["", "VEX."][byte >> 2]
        elif kind == EntryKind.TABLE256:
            opcode_string += "{:02x}".format(byte)
        elif kind == EntryKind.TABLE16:
            opcode_string += "/{:x}{}".format(byte & 7, "mr"[byte >> 3])
        elif kind == EntryKind.TABLE8E:
            opcode_string += "+rm={:x}".format(byte)
        elif kind == EntryKind.TABLE_PREFIX:
            if byte & 4:
                prefix += "VEX."
            prefix += ["NP.", "66.", "F3.", "F2."][byte&3]
        elif kind == EntryKind.TABLE_VEX:
            prefix += "W{}.L{}.".format(byte & 1, byte >> 1)
        else:
            raise Exception("unsupported opcode kind {}".format(kind))
    return prefix + opcode_string

class Table:
    def __init__(self, root_count=1):
        self.data = OrderedDict()
        self.roots = ["root%d"%i for i in range(root_count)]
        for i in range(root_count):
            self.data["root%d"%i] = TrieEntry.table(EntryKind.TABLE_ROOT)
        self.descs = []
        self.descs_map = {}
        self.offsets = {}
        self.annotations = {}

    def _update_table(self, name, idx, entry_name, entry_val):
        old = self.data[name]
        # Don't override existing entries. This only happens on invalid input,
        # e.g. when an opcode is specified twice.
        if old.items[idx]:
            raise Exception("{}/{} set, not overriding to {}".format(name, idx, entry_name))
        self.data[entry_name] = entry_val
        new_items = old.items[:idx] + (entry_name,) + old.items[idx+1:]
        self.data[name] = TrieEntry(old.kind, new_items, None)

    def add_opcode(self, opcode, instr_encoding, root_idx=0):
        name = "t{},{}".format(root_idx, format_opcode(opcode))

        tn = "root%d"%root_idx
        for i in range(len(opcode) - 1):
            # kind is the table kind that we want to point to in the _next_.
            kind, byte = opcode[i+1][0], opcode[i][1]
            # Retain prev_tn name so that we can update it.
            prev_tn, tn = tn, self.data[tn].items[byte]
            if tn is None:
                tn = "t{},{}".format(root_idx, format_opcode(opcode[:i+1]))
                self._update_table(prev_tn, byte, tn, TrieEntry.table(kind))

            if self.data[tn].kind != kind:
                raise Exception("{}, have {}, want {}".format(
                                name, self.data[tn].kind, kind))

        desc_idx = self.descs_map.get(instr_encoding)
        if desc_idx is None:
            desc_idx = self.descs_map[instr_encoding] = len(self.descs)
            self.descs.append(instr_encoding)
        self._update_table(tn, opcode[-1][1], name, TrieEntry.instr(desc_idx))

    def deduplicate(self):
        parents = defaultdict(set)
        for name, entry in self.data.items():
            for child in entry.items:
                parents[child].add(name)

        queue = list(self.data.keys())
        entries = {} # Mapping from entry to name
        while queue:
            # First find new synonyms
            synonyms = {} # Mapping from name to unique name
            for name in queue:
                if self.data[name] in entries:
                    synonyms[name] = entries[self.data[name]]
                    del self.data[name]
                else:
                    entries[self.data[name]] = name
            queue = set.union(set(), *(parents[n] for n in synonyms))
            # Update parents of found synonyms; parents will need to be checked
            # again for synonyms in the next iteration.
            for name in queue:
                entry = self.data[name]
                items = tuple(synonyms.get(v, v) for v in entry.items)
                self.data[name] = entry._replace(items=items)
                for child in items:
                    parents[child].add(name)

    def calc_offsets(self):
        current = 0
        for name, entry in self.data.items():
            if entry.kind == EntryKind.INSTR:
                self.offsets[name] = entry.descidx << 2
            else:
                self.annotations[current] = "%s(%d)" % (name, entry.kind.value)
                self.offsets[name] = current
                current += (len(entry.items) + 3) & ~3
        if current >= 0x8000:
            raise Exception("maximum table size exceeded: {:x}".format(current))

    def _encode_item(self, name):
        return (self.offsets[name] << 1) | self.data[name].kind.value

    def compile(self):
        self.calc_offsets()
        ordered = sorted((off, self.data[k]) for k, off in self.offsets.items() if self.data[k].items)

        data = [0] * (ordered[-1][0] + len(ordered[-1][1].items))
        for off, entry in ordered:
            for i, item in enumerate(entry.items, start=off):
                if item is not None:
                    data[i] = self._encode_item(item)

        stats = dict(Counter(entry.kind for entry in self.data.values()))
        print("%d bytes" % (2*len(data)), stats)
        return tuple(data), self.annotations, [self.offsets[k] for k in self.roots], self.descs

def bytes_to_table(data, notes):
    strdata = tuple(d+"," if type(d) == str else "%#04x,"%d for d in data)
    offs = [0] + sorted(notes.keys()) + [len(data)]
    return "\n".join("".join(strdata[p:c]) + "\n//%04x "%c + notes.get(c, "")
                     for p, c in zip(offs, offs[1:]))

def parse_mnemonics(mnemonics):
    mktree = lambda: defaultdict(mktree)
    tree = mktree()
    for m in mnemonics:
        cur = tree
        for c in m[::-1]:
            cur = cur[c]
    def tree_walk(tree, cur="\0"):
        if not tree:
            yield cur
        else:
            for el, subtree in tree.items():
                for path in tree_walk(subtree, el + cur):
                    yield path
    merged_str = "".join(sorted(tree_walk(tree)))
    cstr = '"' + merged_str[:-1].replace("\0", '\\0') + '"'
    tab = [merged_str.index(m + "\0") for m in mnemonics]
    return cstr, ",".join(map(str, tab))

template = """// Auto-generated file -- do not modify!
#if defined(FD_DECODE_TABLE_DATA)
{hex_table}
#elif defined(FD_DECODE_TABLE_DESCS)
{descs}
#elif defined(FD_DECODE_TABLE_STRTAB1)
{mnemonics[0]}
#elif defined(FD_DECODE_TABLE_STRTAB2)
{mnemonics[1]}
#elif defined(FD_DECODE_TABLE_DEFINES)
{defines}
#else
#error "unspecified decode table"
#endif
"""

def encode_table(entries):
    mnemonics = defaultdict(list)
    mnemonics["FE_NOP"].append(("NP", 0, 0, "0x90"))
    for opcode, desc in entries:
        if desc.mnemonic[:9] == "RESERVED_":
            continue
        if "ONLY32" in desc.flags:
            continue

        opsizes = {8} if "SIZE_8" in desc.flags else {16, 32, 64}
        hasvex, vecsizes = False, {128}

        opc_i = opcode.opc
        if opcode.opcext:
            opc_i |= opcode.opcext << 8
        if opcode.modreg and opcode.modreg[0] is not None:
            opc_i |= opcode.modreg[0] << 8
        opc_flags = ""
        opc_flags += ["","|OPC_0F","|OPC_0F38","|OPC_0F3A"][opcode.escape]
        if opcode.vex:
            hasvex, vecsizes = True, {128, 256}
            opc_flags += "|OPC_VEX"
        if opcode.prefix:
            if opcode.prefix in ("66", "F2", "F3"):
                opc_flags += "|OPC_" + opcode.prefix
            if "USE66" not in desc.flags and opcode.prefix != "NFx":
                opsizes -= {16}
        if opcode.vexl == "IG":
            vecsizes = {0}
        elif opcode.vexl:
            vecsizes -= {128 if opcode.vexl == "1" else 256}
            if opcode.vexl == "1": opc_flags += "|OPC_VEXL"
        if opcode.rexw == "IG":
            opsizes = {0}
        elif opcode.rexw:
            opsizes -= {32 if opcode.rexw == "1" else 64}
            if opcode.rexw == "1": opc_flags += "|OPC_REXW"

        if "DEF64" in desc.flags:
            opsizes -= {32}
        if "INSTR_WIDTH" not in desc.flags and all(op.size != OpKind.SZ_OP for op in desc.operands):
            opsizes = {0}
        if "VSIB" not in desc.flags and all(op.size != OpKind.SZ_VEC for op in desc.operands):
            vecsizes = {0} # for VEX-encoded general-purpose instructions.
        if "ENC_NOSZ" in desc.flags:
            opsizes, vecsizes = {0}, {0}

        # Where to put the operand size in the mnemonic
        separate_opsize = "ENC_SEPSZ" in desc.flags
        prepend_opsize = max(opsizes) > 0 and not separate_opsize
        prepend_vecsize = hasvex and max(vecsizes) > 0 and not separate_opsize

        if "FORCE64" in desc.flags:
            opsizes = {64}
            prepend_opsize = False

        optypes = ["", "", "", ""]
        enc = ENCODINGS[desc.encoding]
        if enc.modrm_idx:
            optypes[enc.modrm_idx^3] = opcode.modreg[1] if opcode.modreg else "rm"
        if enc.modreg_idx: optypes[enc.modreg_idx^3] = "r"
        if enc.vexreg_idx: optypes[enc.vexreg_idx^3] = "r"
        if enc.zeroreg_idx: optypes[enc.zeroreg_idx^3] = "r"
        if enc.imm_control: optypes[enc.imm_idx^3] = " iariioo"[enc.imm_control]
        optypes = product(*(ot for ot in optypes if ot))

        prefixes = [("", "")]
        if "LOCK" in desc.flags:
            prefixes.append(("LOCK_", "|OPC_LOCK"))
        if "ENC_REP" in desc.flags:
            prefixes.append(("REP_", "|OPC_F3"))
        if "ENC_REPCC" in desc.flags:
            prefixes.append(("REPNZ_", "|OPC_F2"))
            prefixes.append(("REPZ_", "|OPC_F3"))

        for opsize, vecsize, prefix, ots in product(opsizes, vecsizes, prefixes, optypes):
            if prefix[1] == "|OPC_LOCK" and ots[0] != "m":
                continue

            imm_size = 0
            if enc.imm_control >= 4:
                if desc.mnemonic == "ENTER":
                    imm_size = 3
                elif "IMM_8" in desc.flags:
                    imm_size = 1
                else:
                    max_imm_size = 4 if desc.mnemonic != "MOVABS" else 8
                    imm_opsize = desc.operands[enc.imm_idx^3].abssize(opsize//8)
                    imm_size = min(max_imm_size, imm_opsize)

            tys = [] # operands that require special handling
            for ot, op in zip(ots, desc.operands):
                if ot == "m":
                    tys.append(0xf)
                elif op.kind == "GP":
                    if (desc.mnemonic == "MOVSX" or desc.mnemonic == "MOVZX" or
                        opsize == 8):
                        tys.append(2 if op.abssize(opsize//8) == 1 else 1)
                    else:
                        tys.append(1)
                else:
                    tys.append({
                        "imm": 0, "SEG": 3, "FPU": 4, "MMX": 5, "XMM": 6,
                        "BND": 8, "CR": 9, "DR": 10,
                    }.get(op.kind, -1))

            tys_i = sum(ty << (4*i) for i, ty in enumerate(tys))
            opc_s = hex(opc_i) + opc_flags + prefix[1]
            if opsize == 16: opc_s += "|OPC_66"
            if opsize == 64 and "DEF64" not in desc.flags and "FORCE64" not in desc.flags: opc_s += "|OPC_REXW"

            # Construct mnemonic name
            mnem_name = {"MOVABS": "MOV", "XCHG_NOP": "XCHG"}.get(desc.mnemonic, desc.mnemonic)
            name = "FE_" + prefix[0] + mnem_name
            if prepend_opsize and not ("DEF64" in desc.flags and opsize == 64):
                name += f"_{opsize}"[name[-1] not in "0123456789":]
            if prepend_vecsize:
                name += f"_{vecsize}"[name[-1] not in "0123456789":]
            for ot, op in zip(ots, desc.operands):
                name += ot.replace("o", "")
                if separate_opsize:
                    name += f"{op.abssize(opsize//8, vecsize//8)*8}"
            mnemonics[name].append((desc.encoding, imm_size, tys_i, opc_s))

    descs = ""
    alt_index = 0
    for mnem, variants in mnemonics.items():
        dedup = []
        for variant in variants:
            # TODO: when adapting to 32-bit mode, handle S encodings.
            if not any(x[:3] == variant[:3] for x in dedup):
                dedup.append(variant)

        enc_prio = ["O", "OA", "OI", "IA", "M", "MI", "MR", "RM"]
        dedup.sort(key=lambda e: (e[1], e[0] in enc_prio and enc_prio.index(e[0])))

        indices = [mnem] + [f"FE_MNEM_MAX+{alt_index+i}" for i in range(len(dedup) - 1)]
        alt_list = indices[1:] + ["0"]
        alt_index += len(alt_list) - 1
        for idx, alt, (enc, immsz, tys_i, opc_s) in zip(indices, alt_list, dedup):
            descs += f"[{idx}] = {{ .enc = ENC_{enc}, .immsz = {immsz}, .tys = {tys_i:#x}, .opc = {opc_s}, .alt = {alt} }},\n"

    mnem_list = sorted(mnemonics.keys())
    mnem_tab = "".join(f"FE_MNEMONIC({m},{i})\n" for i, m in enumerate(mnem_list))
    return mnem_tab, descs

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--32", dest="modes", action="append_const", const=32)
    parser.add_argument("--64", dest="modes", action="append_const", const=64)
    parser.add_argument("--with-undoc", action="store_true")
    parser.add_argument("table", type=argparse.FileType('r'))
    parser.add_argument("decode_mnems", type=argparse.FileType('w'))
    parser.add_argument("decode_table", type=argparse.FileType('w'))
    parser.add_argument("encode_mnems", type=argparse.FileType('w'))
    parser.add_argument("encode_table", type=argparse.FileType('w'))
    args = parser.parse_args()

    entries = []
    for line in args.table.read().splitlines():
        if not line or line[0] == "#": continue
        opcode_string, desc_string = tuple(line.split(maxsplit=1))
        opcode, desc = Opcode.parse(opcode_string), InstrDesc.parse(desc_string)
        if "UNDOC" not in desc.flags or args.with_undoc:
            entries.append((opcode, desc))

    mnemonics = sorted({desc.mnemonic for _, desc in entries})

    decode_mnems_lines = [f"FD_MNEMONIC({m},{i})\n" for i, m in enumerate(mnemonics)]
    args.decode_mnems.write("".join(decode_mnems_lines))

    modes = [32, 64]
    table = Table(root_count=len(args.modes))
    for opcode, desc in entries:
        for i, mode in enumerate(args.modes):
            if "ONLY%d"%(96-mode) not in desc.flags:
                ign66 = opcode.prefix in ("NP", "66", "F2", "F3")
                for opcode_path in opcode.for_trie():
                    table.add_opcode(opcode_path, desc.encode(ign66), i)

    table.deduplicate()
    table_data, annotations, root_offsets, descs = table.compile()

    mnemonics_intel = [m.replace("SSE_", "").replace("MMX_", "")
                        .replace("MOVABS", "MOV")
                        .replace("JMPF", "JMP FAR").replace("CALLF", "CALL FAR")
                        .replace("_S2G", "").replace("_G2S", "")
                        .replace("_CR", "").replace("_DR", "")
                        .lower() for m in mnemonics]

    defines = ["FD_TABLE_OFFSET_%d %d"%k for k in zip(args.modes, root_offsets)]

    decode_table = template.format(
        hex_table=bytes_to_table(table_data, annotations),
        descs="\n".join("{{{0},{1},{2},{3}}},".format(*desc) for desc in descs),
        mnemonics=parse_mnemonics(mnemonics_intel),
        defines="\n".join("#define " + line for line in defines),
    )
    args.decode_table.write(decode_table)

    fe_mnem_list, fe_code = encode_table(entries)
    args.encode_mnems.write(fe_mnem_list)
    args.encode_table.write(fe_code)
