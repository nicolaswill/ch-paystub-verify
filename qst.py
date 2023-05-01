from decimal import Decimal
from pathlib import Path

QST_RECORD_TYPE_VORLAUF = "00"
QST_RECORD_TYPE_PROGRESSIVE_QUELLENSTEUERTARIFE = "06"


def round_05(num: Decimal) -> Decimal:
    to = Decimal("0.05")
    # todo: fix this hack to round 0.xx5 up rather than down
    return Decimal(round((num + Decimal(0.0001)) / to) * to)


class QSTRecord:
    def __init__(self, row: str, format, record_type: str):
        # parse the ASCII row into a dictionary
        # mapping is a dict with the format {field_name: (index_start, index_end)}
        self.format = format
        self.data = {}
        for field_name, (index_start, index_end) in format.items():
            # remove all leading/trailing whitespace
            self.data[field_name] = row[index_start - 1 : index_end].strip()
            if self.data["Recordart"] != record_type:
                raise TypeError()

    def __str__(self):
        return self.data.__str__()


# 3.2. Vorlaufrecord (Recordart 00)
# Beispiel eines Datensatzes:
# 00BE···············20211125···················································································
# Vorlaufrecord, Kanton Bern, erstellt am 25.11.2021
class QSTVorlaufrecord(QSTRecord):
    __format = {
        "Recordart": (1, 2),
        "Kanton": (3, 4),
        "SSL Nummer": (5, 19),
        "Erstellungsdatum": (20, 27),
        "Textzeile 1": (28, 67),
        "Textzeile 2": (68, 107),
        "Code Status": (108, 110),
    }

    def __init__(self, row):
        super().__init__(row, self.__format, QST_RECORD_TYPE_VORLAUF)

    def get_canton_code(self):
        return self.data["Kanton"]

    def get_issue_date(self):
        return self.data["Erstellungsdatum"]


# 3.3. Progressive Quellensteuertarife (Recordart 06)
# Beispiel eines Datensatzes:
# 0601BEB2N·······20220101000650100000005000·0200000000000715···
# Tarifcode, Neuzugang, Kanton Bern, Tarif für verheiratete Alleinverdiener, 2 Kinder, ohne
# Kirchensteuer, Tarif gültig ab 01.01.2022, steuerbares Einkommen ab Fr. 6‘501, Tarifschritt
# Fr. 50.00, 2 Kinder, Steuerbetrag Fr. 0.00 (keine Mindeststeuer), Steuer %-Satz 7,15
class QSTProgressiveQuellensteuertarifeRecord(QSTRecord):
    __format = {
        "Recordart": (1, 2),
        "Transaktionsart": (3, 4),
        "Kanton": (5, 6),
        "QSt-Code": (7, 16),
        "Datum gültig ab": (17, 24),
        "Steuerbares Einkommen ab": (25, 33),
        "Tarifschritt": (34, 42),
        "Code Geschlecht": (43, 43),
        "Anzahl Kinder": (44, 45),
        "Mindeststeuer": (46, 54),
        "Steuer %-Satz": (55, 59),
        "Code Status": (60, 62),
    }

    def __init__(self, row):
        super().__init__(
            row, self.__format, QST_RECORD_TYPE_PROGRESSIVE_QUELLENSTEUERTARIFE
        )

    # Steuer %-Satz
    def tax_rate(self) -> Decimal:
        return Decimal(self.data["Steuer %-Satz"]) / Decimal(10000)

    # Income threshhold
    def income_threshhold(self) -> Decimal:
        return Decimal(self.data["Steuerbares Einkommen ab"]) / Decimal(100)

    # Minimum tax
    def minimum_tax(self) -> Decimal:
        return Decimal(self.data["Mindeststeuer"]) / Decimal(100)

    # Tariff progression step
    def tariff_step(self) -> Decimal:
        return Decimal(self.data["Tarifschritt"]) / Decimal(100)

    # QST-Code
    def qst_code(self) -> str:
        return self.data["QSt-Code"]


def explain_qst_code(code: str):
    part1 = {
        "A": "Single",
        "B": "Married Single-Earner",
        "C": "Married Dual-Earner"
        # note: other codes are not supported by this tool
        # see section 4.2
    }
    part3 = {"N": "Church Tax: No", "Y": "Church Tax: Yes"}
    return f"{part1[code[0]]}, Children: {code[1]}, {part3[code[2]]}"


def is_qst_code_supported(code: str):
    try:
        explain_qst_code(code)
        return True
    except KeyError:
        return False


def get_qst_records(data: list, code: str) -> list:
    records = []
    for row in data:
        try:
            record = QSTProgressiveQuellensteuertarifeRecord(row)
            if record.qst_code() == code:
                records.append(record)
        except TypeError:
            # `row` not a QSTProgressiveQuellensteuertarifeRecord, so skip it
            pass
    return records


def get_qst_code(
    married: bool, single_earner: bool, children: int, church_tax: bool
) -> str:
    char1 = "A" if not married else "B" if single_earner else "C"
    char2 = str(min(max(children, 0), 9))
    char3 = "Y" if church_tax else "N"
    return char1 + char2 + char3


# A highly simplified calculation of source-tax based on:
# 1. Kreisschreiben Nr. 45 der Eidgenössischen Steuerverwaltung (ESTV) über
#    die Quellenbesteuerung des Erwerbseinkommens von Arbeitnehmern (KS 45)
# 2. Richtlinien für Lohndatenverarbeitung, Version 4.0 - Swissdec
#
# This calculation does not account for many factors and is only meant
# to serve as a rough estimation for some common cases and QST-codes.
# See the following resource for a set of more complicated cases:
# https://www.swissdec.ch/de/releases-und-updates/richtlinien-elm/
# Anhang 1 Beispiele QST-Berechnung 20200220_20201202
#
def calculate_withholding_tax_from_table(qst_table: list, income: Decimal) -> Decimal:
    def is_income_in_range(record, income):
        bracket_min = record.income_threshhold()
        bracket_max = bracket_min + record.tariff_step()
        return bracket_min <= income and bracket_max >= income

    record = next(r for r in qst_table if is_income_in_range(r, income))
    tax = round_05(max(record.tax_rate() * income, record.minimum_tax()))
    return tax


def has_annual_qst_model(canton_code: str) -> bool:
    return canton_code in ["FR", "GE", "TI", "VD", "VS"]


def calculate_withholding_tax(
    year: int,
    canton_code: str,
    qst_code: str,
    income: Decimal,
    allow_annual_model: bool = False,
) -> Decimal:
    if has_annual_qst_model(canton_code) and not allow_annual_model:
        # the annual QST model requires cumulative calculations over the year
        raise ValueError("Annual QST models are not supported by this tool.")
    else:
        # open and read the relevant tax table
        with open(
            f"{Path(__file__).parent}/qst/tar{year-2000}{canton_code.lower()}.txt"
        ) as f:
            data = f.read().splitlines()
            # get the relevant records
            records = get_qst_records(data, qst_code)
            # calculate withholding tax
            return calculate_withholding_tax_from_table(records, income)
