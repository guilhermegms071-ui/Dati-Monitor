"""Gera as gravações .snmprec das 8 impressoras simuladas (PROMPT seção 13).

Os OIDs de fabricante usados aqui são EXATAMENTE os dos perfis fornecidos em /profiles
(canon.yaml e konica-minolta.yaml) — nenhum OID proprietário é inventado. Os demais são da
Printer-MIB/Host-Resources-MIB (seção 6.2). Rodar de novo reescreve os arquivos de forma idêntica.

Uso: .venv\\Scripts\\python profiles\\recordings\\sim\\generate.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

# --- OIDs padrão (seção 6.2) -------------------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"
IF_PHYS_ADDRESS = "1.3.6.1.2.1.2.2.1.6.1"
HR_DEVICE_TYPE = "1.3.6.1.2.1.25.3.2.1.2.1"
HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3.1"
HR_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5.1"
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1.1"
HR_PRINTER_ERROR = "1.3.6.1.2.1.25.3.5.1.2.1"
PRT_SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"
PRT_LIFE_COUNT = "1.3.6.1.2.1.43.10.2.1.4.1.1"
PRT_SUPPLIES = "1.3.6.1.2.1.43.11.1.1"
PRT_COLORANT_VALUE = "1.3.6.1.2.1.43.12.1.1.4.1"
PRT_CONSOLE_TEXT = "1.3.6.1.2.1.43.16.5.1.2.1"
PRT_ALERT = "1.3.6.1.2.1.43.18.1.1"
PRINTER_TYPE = "1.3.6.1.2.1.25.3.1.5"
HR_MEMORY_SIZE = "1.3.6.1.2.1.25.2.2.0"  # KBytes
HR_STORAGE = "1.3.6.1.2.1.25.2.3.1"  # .2 tipo, .3 descr, .4 unidade, .5 tamanho, .6 usado
HR_STORAGE_FIXED_DISK = "1.3.6.1.2.1.25.2.1.4"
ENT_FIRMWARE_REV = "1.3.6.1.2.1.47.1.1.1.1.9"

# --- OIDs dos perfis fornecidos -----------------------------------------------------------------
CANON_PRODUCT_NAME = "1.3.6.1.4.1.1602.1.1.1.1.0"
CANON_FIRMWARE = "1.3.6.1.4.1.1602.1.1.1.4.0"
CANON_TABLE_A = "1.3.6.1.4.1.1602.1.11.1.3.1.4"
CANON_TABLE_B_NAMES = "1.3.6.1.4.1.1602.1.11.2.1.1.2"
CANON_TABLE_B_VALUES = "1.3.6.1.4.1.1602.1.11.2.1.1.3"
KM_MODEL = "1.3.6.1.4.1.18334.1.1.1.1.6.2.1.0"
KM_FIRMWARE = "1.3.6.1.4.1.18334.1.1.1.5.5.1.1.3"
KM_TOTAL = "1.3.6.1.4.1.18334.1.1.1.5.7.2.1.1.0"
KM_DUPLEX = "1.3.6.1.4.1.18334.1.1.1.5.7.2.1.3.0"
KM_COPY_MONO = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.1"
KM_PRINT_MONO = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.2"
KM_COPY_COLOR = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.1"
KM_PRINT_COLOR = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.2"
KM_SCAN = "1.3.6.1.4.1.18334.1.1.1.5.7.2.3.1.5.1"

Record = tuple[str, str]  # (tipo snmprec, valor)


def octet(s: str) -> Record:
    return ("4", s)


def hexs(s: str) -> Record:
    return ("4x", s.encode("utf-8").hex())


def integer(n: int) -> Record:
    return ("2", str(n))


def counter(n: int) -> Record:
    return ("65", str(n))


def oid(s: str) -> Record:
    return ("6", s)


def standard(
    *,
    descr: str,
    sys_oid: str,
    name: str,
    mac: str,
    model: str,
    serial: str,
    total: int,
    device_status: int = 2,
    printer_status: int = 3,
    error_hex: str = "0000",
    panel: str = "Pronta",
) -> dict[str, Record]:
    return {
        SYS_DESCR: octet(descr),
        SYS_OBJECT_ID: oid(sys_oid),
        SYS_UPTIME: ("67", "8640000"),
        SYS_NAME: octet(name),
        SYS_LOCATION: octet("Simulador Dati Monitor"),
        IF_PHYS_ADDRESS: ("4x", mac),
        HR_DEVICE_TYPE: oid(PRINTER_TYPE),
        HR_DEVICE_DESCR: octet(model),
        HR_DEVICE_STATUS: integer(device_status),
        HR_PRINTER_STATUS: integer(printer_status),
        HR_PRINTER_ERROR: ("4x", error_hex),
        PRT_SERIAL: octet(serial),
        PRT_LIFE_COUNT: counter(total),
        f"{PRT_CONSOLE_TEXT}.1": octet(panel),
    }


def supplies(rows: list[tuple[int, int, int, str, int, int, int]], colorants: list[str]) -> dict[str, Record]:
    """rows: (colorant_index, class, type, descrição, unidade, capacidade, nível)."""
    out: dict[str, Record] = {}
    for i, (ci, klass, typ, desc, unit, cap, level) in enumerate(rows, start=1):
        out[f"{PRT_SUPPLIES}.3.1.{i}"] = integer(ci)
        out[f"{PRT_SUPPLIES}.4.1.{i}"] = integer(klass)
        out[f"{PRT_SUPPLIES}.5.1.{i}"] = integer(typ)
        out[f"{PRT_SUPPLIES}.6.1.{i}"] = octet(desc)
        out[f"{PRT_SUPPLIES}.7.1.{i}"] = integer(unit)
        out[f"{PRT_SUPPLIES}.8.1.{i}"] = integer(cap)
        out[f"{PRT_SUPPLIES}.9.1.{i}"] = integer(level)
    for ci, name in enumerate(colorants, start=1):
        out[f"{PRT_COLORANT_VALUE}.{ci}"] = octet(name)
    return out


CMYK = ["black", "cyan", "magenta", "yellow"]
PERCENT, TONER, WASTE, OPC, CONSUMED, RECEPTACLE = 19, 3, 4, 9, 3, 4


def canon_color() -> dict[str, Record]:
    d = standard(
        descr="Canon iR-ADV C5540 /P",
        sys_oid="1.3.6.1.4.1.1602.4.7",
        name="CANON-COR-01",
        mac="00aa00000001",
        model="Canon iR-ADV C5540",
        serial="SIMCAN0001",
        total=150000,
        panel="Pronto para imprimir",
    )
    d[CANON_PRODUCT_NAME] = octet("iR-ADV C5540")
    d[CANON_FIRMWARE] = octet("65.23")
    table_a = {
        101: 150000,
        108: 90000,
        112: 5000,
        113: 85000,
        122: 10000,
        123: 50000,
        301: 120000,
        501: 33333,
    }
    for cid, value in table_a.items():
        d[f"{CANON_TABLE_A}.{cid}"] = counter(value)
    d.update(
        supplies(
            [
                (1, CONSUMED, TONER, "Canon Toner Black", PERCENT, 100, 45),
                (2, CONSUMED, TONER, "Canon Toner Cyan", PERCENT, 100, 80),
                (3, CONSUMED, TONER, "Canon Toner Magenta", PERCENT, 100, 12),
                (4, CONSUMED, TONER, "Canon Toner Yellow", PERCENT, 100, 60),
                (0, RECEPTACLE, WASTE, "Waste Toner Container", PERCENT, 100, 30),
            ],
            CMYK,
        )
    )
    return d


def canon_mono() -> dict[str, Record]:
    d = standard(
        descr="Canon iR 1643i",
        sys_oid="1.3.6.1.4.1.1602.4.9",
        name="CANON-PB-02",
        mac="00aa00000002",
        model="Canon iR 1643i",
        serial="SIMCAN0002",
        total=45678,
    )
    d[CANON_PRODUCT_NAME] = octet("iR 1643i")
    # Tabela B (plataforma i-SENSYS): nomes em OCTET STRING hex + valores com o mesmo índice.
    named = [
        ("Total 1", 45678),
        ("Total (Black 1)", 45678),
        ("Print (Total 1)", 30000),
        ("Copy (Total 1)", 15678),
        ("Scan (Total 1)", 2000),
    ]
    for idx, (label, value) in enumerate(named, start=1):
        d[f"{CANON_TABLE_B_NAMES}.{idx}"] = hexs(label)
        d[f"{CANON_TABLE_B_VALUES}.{idx}"] = counter(value)
    d.update(supplies([(1, CONSUMED, TONER, "Canon Toner Black", PERCENT, 100, 70)], ["black"]))
    return d


def konica_color() -> dict[str, Record]:
    # Caso real conferido com o Datacount: total 217031 = PB 100150 + cor 116881.
    d = standard(
        descr="KONICA MINOLTA bizhub C287",
        sys_oid="1.3.6.1.4.1.18334.1.2.1.2.1.1",
        name="KM-COR-03",
        mac="00aa00000003",
        model="KONICA MINOLTA bizhub C287",
        serial="A797019500624",
        total=217031,
        panel="Pronto para copiar",
    )
    d[KM_MODEL] = octet("bizhub C287")
    d[KM_FIRMWARE] = octet("G00-R5")
    d[KM_TOTAL] = counter(217031)
    d[KM_DUPLEX] = counter(5000)
    d[KM_COPY_MONO] = counter(40150)
    d[KM_PRINT_MONO] = counter(60000)
    d[KM_COPY_COLOR] = counter(16881)
    d[KM_PRINT_COLOR] = counter(100000)
    d[KM_SCAN] = counter(7777)
    # Atributos padrão da leitura diária (seção 16.8): memória, disco e firmware (ENTITY-MIB).
    d[HR_MEMORY_SIZE] = integer(2097152)
    d[f"{HR_STORAGE}.2.1"] = oid(HR_STORAGE_FIXED_DISK)
    d[f"{HR_STORAGE}.3.1"] = octet("HDD")
    d[f"{HR_STORAGE}.4.1"] = integer(4096)
    d[f"{HR_STORAGE}.5.1"] = integer(61035156)
    d[f"{HR_STORAGE}.6.1"] = integer(244140)
    d[f"{ENT_FIRMWARE_REV}.1"] = octet("Controller 1.20")
    d.update(
        supplies(
            [
                (1, CONSUMED, TONER, "Toner (Black)", PERCENT, 100, 25),
                (2, CONSUMED, TONER, "Toner (Cyan)", PERCENT, 100, 55),
                (3, CONSUMED, TONER, "Toner (Magenta)", PERCENT, 100, 66),
                (4, CONSUMED, TONER, "Toner (Yellow)", PERCENT, 100, 5),
                (1, CONSUMED, OPC, "Drum Unit (Black)", PERCENT, 100, 70),
            ],
            CMYK,
        )
    )
    return d


def konica_mono() -> dict[str, Record]:
    d = standard(
        descr="KONICA MINOLTA bizhub 367",
        sys_oid="1.3.6.1.4.1.18334.1.2.1.2.1.2",
        name="KM-PB-04",
        mac="00aa00000004",
        model="KONICA MINOLTA bizhub 367",
        serial="SIMKM0004",
        total=88000,
    )
    d[KM_MODEL] = octet("bizhub 367")
    d[KM_FIRMWARE] = octet("G10-R2")
    d[KM_TOTAL] = counter(88000)
    d[KM_COPY_MONO] = counter(30000)
    d[KM_PRINT_MONO] = counter(58000)
    d.update(supplies([(1, CONSUMED, TONER, "Toner (Black)", PERCENT, 100, 90)], ["black"]))
    return d


def generic() -> dict[str, Record]:
    d = standard(
        descr="Impressora generica Printer-MIB (simulador Dati Monitor)",
        sys_oid="1.3.6.1.4.1.8072.3.2.10",
        name="SIM-GENERICA",
        mac="00155d000005",
        model="Generic Laser Printer 5000",
        serial="SIMGEN0005",
        total=48213,
    )
    d.update(supplies([(1, CONSUMED, TONER, "Black Toner Cartridge", PERCENT, 100, 62)], ["black"]))
    return d


def sleeping() -> dict[str, Record]:
    # Em economia de energia; o proxy (sleepy_udp_proxy.py) faz só a 2ª tentativa ser respondida.
    d = standard(
        descr="Impressora em economia de energia (simulador)",
        sys_oid="1.3.6.1.4.1.8072.3.2.10",
        name="SIM-SLEEP-06",
        mac="00aa00000006",
        model="Generic Laser Printer 6000",
        serial="SIMSLEEP06",
        total=12000,
        printer_status=3,
        panel="Sleep",
    )
    d.update(supplies([(1, CONSUMED, TONER, "Black Toner Cartridge", PERCENT, 100, 50)], ["black"]))
    return d


def errors() -> dict[str, Record]:
    # Bits MSB-first do 1º byte: doorOpen (bit 4) = 0x08, jammed (bit 5) = 0x04  ->  0x0C.
    d = standard(
        descr="Impressora com atolamento e porta aberta (simulador)",
        sys_oid="1.3.6.1.4.1.8072.3.2.10",
        name="SIM-ERRO-07",
        mac="00aa00000007",
        model="Generic Laser Printer 7000",
        serial="SIMERR07",
        total=77000,
        device_status=5,
        printer_status=1,
        error_hex="0c00",
        panel="Atolamento de papel",
    )
    d.update(supplies([(1, CONSUMED, TONER, "Black Toner Cartridge", PERCENT, 100, -3)], ["black"]))
    # prtAlertTable (RFC 3805): severidade (.2), treinamento (.3), grupo (.4), índice no grupo (.5),
    # local (.6), código (.7), descrição (.8) e prtAlertTime (.9, TimeTicks).
    d[f"{PRT_ALERT}.2.1.1"] = integer(3)  # critical
    d[f"{PRT_ALERT}.3.1.1"] = integer(4)  # trained
    d[f"{PRT_ALERT}.4.1.1"] = integer(13)  # mediaPath
    d[f"{PRT_ALERT}.5.1.1"] = integer(2)
    d[f"{PRT_ALERT}.6.1.1"] = integer(0)
    d[f"{PRT_ALERT}.7.1.1"] = integer(8)  # jam
    d[f"{PRT_ALERT}.8.1.1"] = octet("Atolamento de papel na bandeja 2")
    d[f"{PRT_ALERT}.9.1.1"] = ("67", "8630000")
    d[f"{PRT_ALERT}.2.1.2"] = integer(3)
    d[f"{PRT_ALERT}.3.1.2"] = integer(3)  # untrained
    d[f"{PRT_ALERT}.4.1.2"] = integer(6)  # cover
    d[f"{PRT_ALERT}.5.1.2"] = integer(1)
    d[f"{PRT_ALERT}.6.1.2"] = integer(0)
    d[f"{PRT_ALERT}.9.1.2"] = ("67", "8635000")
    d[f"{PRT_ALERT}.7.1.2"] = integer(3)  # coverOpen
    d[f"{PRT_ALERT}.8.1.2"] = octet("Porta frontal aberta")
    return d


def regressing(total: int = 500000) -> dict[str, Record]:
    d = standard(
        descr="Impressora com contador que regride (simulador)",
        sys_oid="1.3.6.1.4.1.8072.3.2.10",
        name="SIM-REGR-08",
        mac="00aa00000008",
        model="Generic Laser Printer 8000",
        serial="SIMREG08",
        total=total,
    )
    d.update(supplies([(1, CONSUMED, TONER, "Black Toner Cartridge", PERCENT, 100, 40)], ["black"]))
    return d


def _key(o: str) -> tuple[int, ...]:
    return tuple(int(x) for x in o.split("."))


def render(data: dict[str, Record]) -> str:
    return "".join(f"{o}|{t}|{v}\n" for o, (t, v) in sorted(data.items(), key=lambda kv: _key(kv[0])))


PRINTERS = {
    "01-canon-cor": canon_color,
    "02-canon-pb": canon_mono,
    "03-konica-cor": konica_color,
    "04-konica-pb": konica_mono,
    "05-generica": generic,
    "06-economia": sleeping,
    "07-erros": errors,
    "08-regressao": regressing,
}


def main() -> None:
    for folder, build in PRINTERS.items():
        path = HERE / folder
        path.mkdir(exist_ok=True)
        (path / "public.snmprec").write_text(render(build()), encoding="utf-8", newline="\n")
    # Segunda versão do 08 (contador menor) usada pelo teste de regressão.
    regressed = HERE / "08-regressao" / "regressed.snmprec.txt"
    regressed.write_text(render(regressing(400000)), encoding="utf-8", newline="\n")
    print(f"{len(PRINTERS)} gravações geradas em {HERE}")  # noqa: T201


if __name__ == "__main__":
    main()
