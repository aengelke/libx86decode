
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <fadec-enc.h>

enum {
    OPC_0F = 1 << 16,
    OPC_0F38 = (1 << 17) | OPC_0F,
    OPC_0F3A = (1 << 18) | OPC_0F,
    OPC_66 = 1 << 19,
    OPC_F2 = 1 << 20,
    OPC_F3 = 1 << 21,
    OPC_REXW = 1 << 22,
    OPC_REXR = 1 << 23,
    OPC_REXX = 1 << 24,
    OPC_REXB = 1 << 25,
    OPC_REX = 1 << 26,
    OPC_LOCK = 1 << 28,
};

static bool op_mem(uint64_t op) { return (op & 0x8000000000000000) != 0; }
static bool op_reg(uint64_t op) { return (op & 0x8000000000000000) == 0; }
static bool op_reg_gpl(uint64_t op) { return (op & 0xfffffffffffffff0) == 0x100; }
static bool op_reg_gph(uint64_t op) { return (op & 0xfffffffffffffffc) == 0x204; }
static int64_t op_mem_offset(uint64_t op) { return (int32_t) (op & 0x00000000ffffffff); }
static unsigned op_mem_base(uint64_t op) { return (op & 0x00000fff00000000) >> 32; }
static unsigned op_mem_idx(uint64_t op) { return (op & 0x00fff00000000000) >> 44; }
static unsigned op_mem_scale(uint64_t op) { return (op & 0x0f00000000000000) >> 56; }
static unsigned op_reg_idx(uint64_t op) { return (op & 0x00000000000000ff); }
static bool op_imm_n(uint64_t imm, unsigned immsz) {
    if (immsz == 1 && (uint64_t) (int64_t) (int8_t) imm != imm) return false;
    if (immsz == 2 && (uint64_t) (int64_t) (int16_t) imm != imm) return false;
    if (immsz == 4 && (uint64_t) (int64_t) (int32_t) imm != imm) return false;
    return true;
}

static
int
enc_opc(uint8_t** buf, uint64_t opc)
{
    if (opc & OPC_66) *(*buf)++ = 0x66;
    if (opc & OPC_F2) *(*buf)++ = 0xF2;
    if (opc & OPC_F3) *(*buf)++ = 0xF3;
    if (opc & (OPC_REX|OPC_REXW|OPC_REXR|OPC_REXX|OPC_REXB))
    {
        unsigned rex = 0x40;
        if (opc & OPC_REXW) rex |= 8;
        if (opc & OPC_REXR) rex |= 4;
        if (opc & OPC_REXX) rex |= 2;
        if (opc & OPC_REXB) rex |= 1;
        *(*buf)++ = rex;
    }
    if (opc & OPC_0F) *(*buf)++ = 0x0F;
    if ((opc & OPC_0F38) == OPC_0F38) *(*buf)++ = 0x38;
    if ((opc & OPC_0F3A) == OPC_0F3A) *(*buf)++ = 0x3A;
    *(*buf)++ = opc & 0xff;
    if ((opc & 0xc000) == 0xc000) *(*buf)++ = (opc >> 8) & 0xff;
    return 0;
}

static
int
enc_imm(uint8_t** buf, uint64_t imm, unsigned immsz)
{
    if (!op_imm_n(imm, immsz)) return -1;
    for (unsigned i = 0; i < immsz; i++)
        *(*buf)++ = imm >> 8 * i;
    return 0;
}

static
int
enc_o(uint8_t** buf, uint64_t opc, uint64_t op0)
{
    if (op_reg_idx(op0) & 0x8) opc |= OPC_REXB;

    bool has_rex = !!(opc & (OPC_REX|OPC_REXW|OPC_REXR|OPC_REXX|OPC_REXB));
    if (has_rex && op_reg_gph(op0)) return -1;

    if (enc_opc(buf, opc)) return -1;
    *(*buf - 1) = (*(*buf - 1) & 0xf8) | (op_reg_idx(op0) & 0x7);
    return 0;
}

static
int
enc_mr(uint8_t** buf, uint64_t opc, uint64_t op0, uint64_t op1)
{
    // If !op_reg(op1), it is a constant value for ModRM.reg
    if (op_reg(op0) && (op_reg_idx(op0) & 0x8)) opc |= OPC_REXB;
    if (op_mem(op0) && (op_mem_base(op0) & 0x8)) opc |= OPC_REXB;
    if (op_mem(op0) && (op_mem_idx(op0) & 0x8)) opc |= OPC_REXX;
    if (op_reg(op1) && op_reg_idx(op1) & 0x8) opc |= OPC_REXR;

    bool has_rex = !!(opc & (OPC_REX|OPC_REXW|OPC_REXR|OPC_REXX|OPC_REXB));
    if (has_rex && (op_reg_gph(op0) || op_reg_gph(op1))) return -1;

    if (enc_opc(buf, opc)) return -1;
    int mod = 0, reg = op1 & 7, rm;
    int scale = 0, idx = 4, base;
    bool withsib = false, mod0off = false;
    if (op_reg(op0))
    {
        mod = 3;
        rm = op_reg_idx(op0) & 7;
    }
    else
    {
        if (op_mem_idx(op0))
        {
            if (!op_reg_gpl(op_mem_idx(op0))) return -1;
            if (op_reg_idx(op_mem_idx(op0)) == 4) return -1;
            idx = op_mem_idx(op0) & 7;
            int scalabs = op_mem_scale(op0);
            if (scalabs == 1) scale = 0;
            else if (scalabs == 2) scale = 1;
            else if (scalabs == 4) scale = 2;
            else if (scalabs == 8) scale = 3;
            else return -1;
            withsib = true;
        }

        if (!op_mem_base(op0))
        {
            rm = 5;
            mod0off = true;
            withsib = true;
        }
        else if (op_mem_base(op0) == FE_IP)
        {
            rm = 5;
            mod0off = true;
            if (withsib) return -1;
        }
        else
        {
            if (!op_reg_gpl(op_mem_base(op0))) return -1;
            rm = op_reg_idx(op_mem_base(op0)) & 7;
            if (rm == 5) mod = 1;
        }

        if (op_mem_offset(op0) && op_imm_n(op_mem_offset(op0), 1) && !mod0off)
            mod = 1;
        else if (op_mem_offset(op0) && !mod0off)
            mod = 2;

        if (withsib || rm == 4)
        {
            base = rm;
            rm = 4;
        }
    }

    *(*buf)++ = (mod << 6) | (reg << 3) | rm;
    if (mod != 3 && rm == 4)
        *(*buf)++ = (scale << 6) | (idx << 3) | base;
    if (mod == 1) return enc_imm(buf, op_mem_offset(op0), 1);
    if (mod == 2 || mod0off) return enc_imm(buf, op_mem_offset(op0), 4);
    return 0;
}

typedef enum {
    ENC_INVALID,
    ENC_NP,
    ENC_M,
    ENC_M1,
    ENC_MI,
    ENC_MC,
    ENC_MR,
    ENC_RM,
    ENC_RMA,
    ENC_MRI,
    ENC_RMI,
    ENC_MRC,
    ENC_I,
    ENC_IA,
    ENC_O,
    ENC_OI,
    ENC_OA,
    ENC_AO,
    ENC_A,
    ENC_D,
    ENC_FD,
    ENC_TD,
    ENC_RVM,
    ENC_RVMI,
    ENC_RVMR,
    ENC_RMV,
    ENC_VM,
    ENC_VMI,
    ENC_MVR,
} Encoding;

int
fe_enc64_impl(uint8_t** buf, uint64_t mnem, FeOp op0, FeOp op1, FeOp op2, FeOp op3)
{
    uint8_t* buf_start = *buf;
    Encoding enc = ENC_INVALID;
    uint64_t opc;
    unsigned immsz;
    unsigned gp8ops = 0;

    switch (mnem & FE_MNEM_MASK)
    {
    default: goto fail;
#include <fadec-enc-cases.inc>
    }

encode:
    if (gp8ops)
    {
        if ((gp8ops & 1) && op_reg_gpl(op0) && op0 >= FE_SP && op0 <= FE_DI)
            opc |= OPC_REX;
        if ((gp8ops & 2) && op_reg_gpl(op1) && op1 >= FE_SP && op1 <= FE_DI)
            opc |= OPC_REX;
        if ((gp8ops & 4) && op_reg_gpl(op2) && op2 >= FE_SP && op2 <= FE_DI)
            opc |= OPC_REX;
        if ((gp8ops & 8) && op_reg_gpl(op3) && op3 >= FE_SP && op3 <= FE_DI)
            opc |= OPC_REX;
    }

    if (mnem & FE_ADDR32)
        *(*buf)++ = 0x67;
    if (mnem & 0x70000)
        *(*buf)++ = (0x65643e362e2600 >> (8 * ((mnem & 0x70000) >> 16))) & 0xff;

    switch (enc)
    {
    case ENC_NP:
    case ENC_A:
        if (enc_opc(buf, opc)) goto fail;
        break;
    case ENC_M:
    case ENC_M1:
    case ENC_MC:
        if (enc_mr(buf, opc, op0, (opc & 0xff00) >> 8)) goto fail;
        break;
    case ENC_MI:
        if (enc_mr(buf, opc, op0, (opc & 0xff00) >> 8)) goto fail;
        if (enc_imm(buf, op1, immsz)) goto fail;
        break;
    case ENC_MR:
    case ENC_MRC:
        if (enc_mr(buf, opc, op0, op1)) goto fail;
        break;
    case ENC_RM:
    case ENC_RMA:
        if (enc_mr(buf, opc, op1, op0)) goto fail;
        break;
    case ENC_MRI:
        if (enc_mr(buf, opc, op0, op1)) goto fail;
        if (enc_imm(buf, op2, immsz)) goto fail;
        break;
    case ENC_RMI:
        if (enc_mr(buf, opc, op1, op0)) goto fail;
        if (enc_imm(buf, op2, immsz)) goto fail;
        break;
    case ENC_I:
        if (enc_opc(buf, opc)) goto fail;
        if (enc_imm(buf, op0, immsz)) goto fail;
        break;
    case ENC_IA:
        if (enc_opc(buf, opc)) goto fail;
        if (enc_imm(buf, op1, immsz)) goto fail;
        break;
    case ENC_O:
    case ENC_OA:
        if (enc_o(buf, opc, op0)) goto fail;
        break;
    case ENC_OI:
        if (enc_o(buf, opc, op0)) goto fail;
        if (enc_imm(buf, op1, immsz)) goto fail;
        break;
    case ENC_AO:
        if (enc_o(buf, opc, op1)) goto fail;
        break;
    case ENC_D:
        if (enc_opc(buf, opc)) goto fail;
        if (enc_imm(buf, op0 == FE_JMP_RESERVE ? 0 : op0 - ((int64_t) *buf + immsz), immsz)) goto fail;
        break;
    case ENC_INVALID:
    default:
        goto fail;
    }

    return 0;

fail:
    *buf = buf_start; // Don't advance buffer on error
    return -1;
}