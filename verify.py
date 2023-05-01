import argparse
import datetime
import itertools
import qst
import re
import tabula
from typing import Tuple
from dataclasses import dataclass
from pathlib import Path
from PyPDF2 import PdfReader
from decimal import Decimal

CRED = "\033[91m"
CGRN = "\033[92m"
CYLW = "\033[93m"
CEND = "\033[0m"


def round_05(num: Decimal) -> Decimal:
    to = Decimal("0.05")
    # TODO: fix this calc
    return Decimal(round((num + Decimal(0.0001)) / to) * to)


def str_to_dec(s: str) -> Decimal:
    return Decimal(s.replace("'", "").replace("%", "").strip())


def print_pass(msg: str):
    print(CGRN + "PASS: " + msg + CEND)


def print_fail(msg: str):
    print(CRED + "FAIL: " + msg + CEND)


def print_warn(msg: str):
    print(CYLW + "WARN: " + msg + CEND)


def print_note(msg: str):
    print("NOTE: " + msg)


def get_oasi_rate(year: int) -> Decimal:
    """
    Returns the OASI (AHV+IV+EO) employee contribution rate for a given year.
    The total rate contributed is double the rate returned (employee + employer).
    """
    OASI_rates = {
        2020: Decimal("0.05275"),
        2021: Decimal("0.05300"),
        2022: Decimal("0.05300"),
        2023: Decimal("0.05300"),
    }
    return OASI_rates[year]


def get_oasi_contribution(year: int, gross_salary: Decimal) -> Decimal:
    """
    Returns the OASI (AHV+IV+EO) employee contribution for a given year and gross salary.
    The total sum contributed is double the sum returned (employee + employer).
    """
    return round_05(gross_salary * get_oasi_rate(year))


def get_bvg_minimum_salary(year: int) -> Decimal:
    """
    Returns the minimum salary for BVG (second pillar pension) contributions for a given year.
    """
    BVG_minimum_salaries = {
        2020: Decimal("21330"),
        2021: Decimal("21510"),
        2022: Decimal("21510"),
        2023: Decimal("22050"),
    }
    return BVG_minimum_salaries[year]


def get_bvg_monthly_minimum_salary(year: int) -> Decimal:
    return get_bvg_minimum_salary(year) / Decimal(12)


def get_ahv_coordination_deduction(year: int) -> Decimal:
    """
    Returns the AHV coordination deduction (Koordinationsabzug) for a given year.
    """
    AHV_coordination_deductions = {
        2020: Decimal("24885"),
        2021: Decimal("25095"),
        2022: Decimal("25095"),
        2023: Decimal("25725"),
    }
    return AHV_coordination_deductions[year]


def get_bvg_rate(age_at_eoy: int) -> Decimal:
    """
    Returns the total BVG (second pillar pension) contribution rate given an age at the end of the year.
    The rate returned is the sum of the employee and employer rates (split 50/50).
    """
    if age_at_eoy < 25:
        return Decimal("0")
    elif age_at_eoy < 35:
        return Decimal("0.07")
    elif age_at_eoy < 45:
        return Decimal("0.10")
    elif age_at_eoy < 55:
        return Decimal("0.15")
    else:
        return Decimal("0.18")


def get_bvg_savings_contribution(
    year: int, age_at_eoy: int, annual_base_salary: Decimal
) -> Decimal:
    """
    Returns the BVG (2nd pillar pension) savings contribution for a year, age at the end of that year, and base salary.
    The sum returned is the sum of the employee and employer contributions (split 50/50).
    """
    if annual_base_salary < get_bvg_minimum_salary(year):
        return Decimal(0)
    ahv_coordination_deduction = get_ahv_coordination_deduction(year)
    insured_salary1 = annual_base_salary - ahv_coordination_deduction
    insured_salary2 = (
        min(annual_base_salary, Decimal(300000))
        - (Decimal(3) * ahv_coordination_deduction)
        if age_at_eoy >= 35
        else Decimal(0)
    )
    rate = get_bvg_rate(age_at_eoy)
    bvg_savings_contribution = round_05((insured_salary1 + insured_salary2) * rate)
    return bvg_savings_contribution


def get_bvg_monthly_savings_contribution(
    year: int, age_at_eoy: int, annual_base_salary: Decimal
) -> Decimal:
    return round_05(
        get_bvg_savings_contribution(year, age_at_eoy, annual_base_salary) / Decimal(12)
    )


def get_max_insured_salary(year: int) -> Decimal:
    """
    Returns the maximum insured salary for BVG (2nd pillar pension) contributions for a given year.
    """
    del year  # unused for now but kept for future support
    return Decimal(148200)


def get_max_monthly_insured_salary(year: int) -> Decimal:
    """
    Returns the maximum monthly insured salary for BVG (2nd pillar pension) contributions for a given year.
    """
    return get_max_insured_salary(year) / Decimal(12)


def get_ui_rate(year: int) -> Decimal:
    del year  # unused for now but kept for future support
    return Decimal("0.011")


def get_ui_contribution(year: int, gross_salary_si: Decimal) -> Decimal:
    """
    Returns the UI (ALV) contribution for a given year and gross salary.
    The total sum contributed is double the sum returned (employee + employer).
    """
    insured_salary = min(gross_salary_si, get_max_monthly_insured_salary(year))
    return round_05(insured_salary * get_ui_rate(year))


def get_sui_contribution(year: int, gross_salary_si: Decimal) -> Decimal:
    """
    Returns the SUI (ALV Solidarity) contribution for a given year and gross salary.
    The total sum contributed is double the sum returned (employee + employer).
    """
    if gross_salary_si <= get_max_monthly_insured_salary(year) or year > 2022:
        return Decimal(0)
    applicable_salary = gross_salary_si - get_max_monthly_insured_salary(year)
    return round_05(applicable_salary * Decimal(0.005))


@dataclass
class EmployeeData:
    birth_year: int
    withholding_tax: bool
    base_salary: Decimal
    pension_contribution: Decimal = None


@dataclass
class SalaryComponent:
    si_exempt: bool = False  # social insurance exempt
    external_payment: bool = False  # not included in the payslip wage paid


known_gross_salary_entries = {
    "Monthly wage": SalaryComponent(),
    "Benefits stipend": SalaryComponent(),
    "Communication stipend": SalaryComponent(),  # until end of 2022
    "Wellness stipend": SalaryComponent(),  # until end of 2022
    "Full Benefit Reimbursement": SalaryComponent(),  # from start of 2023
    "ESPP gain": SalaryComponent(external_payment=True),
    "Stock Award": SalaryComponent(external_payment=True),
    "Bonus": SalaryComponent(),
    "Commission": SalaryComponent(),
    "Child and education allowances": SalaryComponent(si_exempt=True),
}

known_social_deduction_entries: list[str] = [
    "OASI contribution",  # AHV IV EO
    "UI contribution",  # ALV
    "SUI contribution",  # ALV Solidaritätsprozent (until end of 2022)
    "SUVA contribution",  # NBU
    "DSA contribution",  # KTG
]

known_pension_deduction_entries: list[str] = [
    "PF/LOB contrib. fixed men",  # BVG
    "PF/LOB contrib. fixed women",  # BVG
]

known_tax_deduction_entry = "Withholding tax deduction"  # Quellensteuer

known_deduction_entries: list[str] = list(
    itertools.chain(
        known_social_deduction_entries,
        known_pension_deduction_entries,
        [known_tax_deduction_entry],
    )
)


class Payslip:
    def __init__(self, payslip_path: str, employee: EmployeeData):
        self.__payslip_path = payslip_path
        self.payslip_name = Path(payslip_path).name
        self.payslip_date = self.__get_date()
        self.df = self.__get_dataframe()
        self.employee = employee

    def __get_date(self):
        reader = PdfReader(self.__payslip_path)
        raw_payslip_date = re.search(
            r"\d{2}(\.|-)\d{2}(\.|-)\d{4}", reader.pages[0].extract_text()
        )[0]
        return datetime.datetime.strptime(raw_payslip_date, "%d.%m.%Y").date()

    def __get_dataframe(self):
        df = tabula.read_pdf(self.__payslip_path, pages="all")[0]
        df.drop(
            labels=["Unnamed: 0", "Unnamed: 1", "Unnamed: 2", "Unnamed: 3"],
            axis=1,
            inplace=True,
        )
        df.fillna(0, inplace=True)
        df.set_index("Payroll type", inplace=True)
        return df

    def __str__(self):
        return self.df.to_string()

    @staticmethod
    def validate_calc(
        name: str,
        actual: Decimal,
        expected: Decimal,
        tolerance: Decimal = Decimal(0),
    ) -> bool:
        difference = actual - expected
        if abs(difference) > tolerance:
            print_fail(f"{name}\nExpected: {expected}\nActual: {actual}")
            return False
        else:
            print_pass(name)
            return True

    def get_val(
        self, row: str, col: str = "Total", throw_if_not_found=False
    ) -> Decimal:
        try:
            return str_to_dec(str(self.df.at[row, col]))
        except KeyError as err:
            if throw_if_not_found:
                raise (err)
            else:
                return Decimal(0)

    def row_exists(self, row: str) -> bool:
        return row in self.df.index

    def val_exists(self, row: str, col: str, only_non_zero=False) -> bool:
        try:
            val = self.get_val(row, col, throw_if_not_found=True)
        except KeyError:
            return False
        else:
            return not only_non_zero or val != Decimal(0)

    def get_df_slice(
        self,
        start_row: str = None,
        end_row: str = None,
        include_start: bool = False,
        include_end: bool = False,
    ):
        # get the indices of the start and end rows
        start = (
            (self.df.index.get_loc(start_row) + (0 if include_start else 1))
            if start_row
            else 0
        )
        end = (
            (self.df.index.get_loc(end_row) + (1 if include_end else 0))
            if end_row
            else len(self.df.index) - 1
        )
        # return a dataframe slice between the start and end rows
        return self.df.iloc[start:end]

    def get_subtotal_slice(self, total_row: str):
        def is_next_row_subtotal(row_index: str):
            if row_index < len(self.df.index) - 1:
                subsequent_row = self.df.index[row_index + 1]
                return self.val_exists(subsequent_row, "Sub-total", only_non_zero=True)
            else:
                return False

        start_index = self.df.index.get_loc(total_row)
        if is_next_row_subtotal(start_index):
            start_index += 1
            end_index = start_index
            while is_next_row_subtotal(end_index):
                end_index += 1
            return self.df.iloc[start_index : end_index + 1]
        else:
            return None

    @staticmethod
    def get_col_sum(section, col: str) -> Decimal:
        # iterate through the column and sum the values
        # note: don't use .sum as there is an internal float64 conversion
        result = Decimal(0)
        for x in section[col].tolist():
            result += Decimal(str(x).replace("'", ""))
        return result


class SupplementaryPayslip(Payslip):
    def __init__(self, payslip_path: str, employee: EmployeeData):
        super().__init__(payslip_path, employee)
        if self.row_exists("Monthly wage"):
            raise ValueError(
                "Supplementary payslip input contains a 'Monthly wage' row."
            )
        # TODO: generalize this class
        # for now, verify that a "Stock Award" row exists
        if not self.row_exists("Stock Award"):
            raise ValueError(
                "Supplementary payslip input does not contain a 'Stock Award' row."
            )

    def validate_stock_withholding(self, stock_award: Decimal):
        stock_award_withholding_rate_by_year = {
            2020: Decimal(0.0623),  # unconfirmed
            2021: Decimal(0.0623),  # unconfirmed
            2022: Decimal(0.0623),  # confirmed
            2023: Decimal(0.0630),  # confirmed
        }
        stock_award_withholding_rate = stock_award_withholding_rate_by_year[
            self.payslip_date.year
        ]
        stated_withholding = self.get_val("Already settled social security")
        expected_withholding = round_05(stock_award * stock_award_withholding_rate)
        Payslip.validate_calc(
            "Stock award social security withholding",
            stated_withholding,
            expected_withholding,
            # Shares Withheld is rounded to the nearest thousandth of a share:
            # Shares Withheld = round(Shares Awarded * Withholding Rate, 3)
            # Withheld Value = (Shares Withheld * Share FMV * FX-Rate)
            # Therefore, allow tolerance of the value of a thousandth of a share
            # assuming a max share value price 1000 CHF ($MSFT=~290 CHF 01.05.2023)
            tolerance=Decimal(1.0),
        )

    def validate(self):
        # this method currently only supports validation of stock payslips
        # and isn't architecturally integrated into WagePayslip.validate()
        # e.g. for verifying gross/net calculations.
        self.validate_stock_withholding(self.get_val("Stock Award"))


class WagePayslip(Payslip):
    def __init__(
        self,
        payslip_path: str,
        employee: EmployeeData,
        supplements: list[SupplementaryPayslip] = None,
    ):
        super().__init__(payslip_path, employee)
        self.supplements = supplements
        # ensure that this payslip and its supplements all have the same date
        for supplement in supplements:
            if supplement.payslip_date != self.payslip_date:
                raise ValueError(
                    f"Supplement payslip date {supplement.payslip_date} does not match primary payslip date {self.payslip_date}."
                )
        # validate wage payslip format
        if not self.row_exists("Monthly wage"):
            raise ValueError(
                "Wage payslip input does not contain a 'Monthly wage' row."
            )

    def get_aggregate_val_sum(self, row: str, col: str = "Total"):
        sum = Decimal(0)
        for payslip in itertools.chain([self], self.supplements):
            # sum the gross salary components (Decimal(0) if not found)
            sum += payslip.get_val(row, col)
        return sum

    def validate_gross_salary(self) -> Tuple[Decimal, Decimal]:
        """
        Validates the gross salary. Returns a tuple of the gross salary and social-insurance salary.
        This method calculates the aggregate gross salaries from the primary payslip and its supplements.
        """
        total_gross_salary = Decimal(0)
        total_gross_salary_si = Decimal(0)
        total_gross_salary_non_cash = Decimal(0)
        # loop through the main payslip and each supplementary payslip
        for payslip in itertools.chain([self], self.supplements):
            # sum the gross salary components
            gross_salary_entries = payslip.get_df_slice(end_row="Gross salary")
            gross_salary_sum = Payslip.get_col_sum(gross_salary_entries, "Total")
            total_gross_salary += gross_salary_sum
            total_gross_salary_si += gross_salary_sum
            # validate the entries and adjust the social-insurance salary
            for entry in gross_salary_entries.index:
                if entry not in known_gross_salary_entries:
                    print_warn(f'Unknown gross salary component in payslip: "{entry}"')
                else:
                    if known_gross_salary_entries[entry].si_exempt:
                        total_gross_salary_si -= payslip.get_val(entry)
                    if known_gross_salary_entries[entry].external_payment:
                        total_gross_salary_non_cash += payslip.get_val(entry)
            # validate the payslip gross salary calculation
            Payslip.validate_calc(
                f"Stated gross salary calculation [{payslip.payslip_name}]",
                gross_salary_sum,
                payslip.get_val("Gross salary"),
            )
        return total_gross_salary, total_gross_salary_si, total_gross_salary_non_cash

    def validate_monthly_base_salary(
        self, expected_annual_base_salary: Decimal = None
    ) -> Decimal:
        stated_monthly_base_salary = self.get_val("Monthly wage")
        if not expected_annual_base_salary:
            print_warn("Expected base salary not specified as an argument.")
            print_warn(
                f"Verify your annual salary (error up to 0.60): "
                f"{stated_monthly_base_salary * Decimal(12)}"
            )
        else:
            Payslip.validate_calc(
                "Base salary",
                stated_monthly_base_salary,
                round_05(self.employee.base_salary / Decimal(12)),
            )
        return stated_monthly_base_salary

    def validate_oasi_contribution(self, total_gross_salary_si: Decimal):
        """
        Validates the OASI (AHV/IV/EO) contribution.
        """
        stated_oasi_contributions = self.get_aggregate_val_sum("OASI contribution")
        expected_oasi_contributions = -get_oasi_contribution(
            self.payslip_date.year, total_gross_salary_si
        )
        Payslip.validate_calc(
            "AHV/IV/EO contributions",
            stated_oasi_contributions,
            expected_oasi_contributions,
        )
        return stated_oasi_contributions

    def validate_ui_contribution(self, total_gross_salary_si: Decimal):
        """
        Validates the unemployment insurance contribution.
        """
        # validate UI / ALV 1
        stated_ui_contributions = self.get_aggregate_val_sum("UI contribution")
        expected_ui_contributions = -get_ui_contribution(
            self.payslip_date.year, total_gross_salary_si
        )
        Payslip.validate_calc(
            "UI contributions",
            stated_ui_contributions,
            expected_ui_contributions,
        )
        # validate SUI / ALV 2 (Solidaritätsprozent, applicable before 2023)
        stated_sui_contributions = self.get_aggregate_val_sum("SUI contribution")
        if self.payslip_date.year < 2023:
            expected_sui_contributions = -get_sui_contribution(
                self.payslip_date.year, total_gross_salary_si
            )
            Payslip.validate_calc(
                "SUI contributions",
                stated_sui_contributions,
                expected_sui_contributions,
            )
        elif stated_sui_contributions != Decimal(0):
            print_fail("SUI entry found in payslip after 2022.")

    def validate_suva_contributions(self, total_gross_salary_si: Decimal):
        # check if the SUVA contribution is present
        if self.get_aggregate_val_sum("SUVA contribution") == Decimal(0):
            print_fail("SUVA contribution not found in payslip.")
        else:
            # this figure might be computable with the following info:
            # https://www.suva.ch/de-ch/download/dokument/praemientarif-2022--335.D%2822%29
            print_note(
                "Skipping SUVA contribution calculation validation (check not yet implemented)."
            )

    def validate_dsa_contributions(self, total_gross_salary_si: Decimal):
        # check if the DSA contribution is present
        if self.get_aggregate_val_sum("DSA contribution") == Decimal(0):
            print_fail("DSA contribution not found in payslip.")
        else:
            # this figure might be computable given a Krankentaggeldversicherung contract
            print_note(
                "Skipping DSA contribution calculation validation (check not yet implemented)."
            )

    def validate_net_salary_calculations(
        self, total_gross_salary: Decimal, total_gross_salary_si: Decimal
    ):
        total_deductions = Decimal(0)
        # loop through the main payslip and each supplementary payslip
        for payslip in itertools.chain([self], self.supplements):
            # sum the deduction components
            deduction_entries = payslip.get_df_slice("Gross salary", "Net salary")
            deduction_sum = Payslip.get_col_sum(deduction_entries, "Total")
            total_deductions += deduction_sum
            # validate the deduction components
            for entry in deduction_entries.index:
                if entry not in known_deduction_entries and payslip.val_exists(
                    entry, "Total", True
                ):
                    print_warn(f'Unknown deduction in payslip: "{entry}"')
            # validate the payslip gross salary calculation
            expected_net_salary = payslip.get_val("Gross salary") + deduction_sum
            Payslip.validate_calc(
                f"Stated net salary calculation [{payslip.payslip_name}]",
                payslip.get_val("Net salary"),
                expected_net_salary,
            )

    def validate_bvg_contributions(self, annual_base_salary: Decimal):
        if annual_base_salary < get_bvg_minimum_salary(self.payslip_date.year):
            if self.get_aggregate_val_sum("PF/LOB contrib. fixed") != Decimal(0):
                print_fail(
                    "Pension contribution found in payslip with salary below BVG minimum."
                )
            else:
                print_warn(
                    "Salary below BVG minimum, no pension contribution expected."
                )
            return

        stated_bvg_contrib = Decimal(0)
        for pf_entry in known_pension_deduction_entries:
            stated_bvg_contrib += self.get_aggregate_val_sum(pf_entry)
        specified_bvg_contrib = self.employee.pension_contribution
        if (
            specified_bvg_contrib
        ):  # could be 'None' if no pension contribution specified
            Payslip.validate_calc(
                "PF/LOB contrib. fixed",
                stated_bvg_contrib,
                -specified_bvg_contrib,
            )
        else:
            print_warn(
                "No expected pension contribution specified. Performing heuristic check:"
            )
            employee_age_at_eoy = self.payslip_date.year - self.employee.birth_year
            computed_savings_contrib = -get_bvg_monthly_savings_contribution(
                self.payslip_date.year, employee_age_at_eoy, annual_base_salary
            ) / Decimal(2)
            computed_savings_contrib_next_bracket = (
                -get_bvg_monthly_savings_contribution(
                    self.payslip_date.year,
                    employee_age_at_eoy + 10,
                    annual_base_salary,
                )
                / Decimal(2)
            )
            if stated_bvg_contrib >= computed_savings_contrib:
                print_fail(
                    "Pension contribution is implausibly low. Manually check your pension certificate."
                )
            elif (
                employee_age_at_eoy < 55
                and stated_bvg_contrib <= computed_savings_contrib_next_bracket
            ):
                print_fail(
                    "Pension contribution is implausibly high. Manually check your pension certificate."
                )
            else:
                print_pass(f"Pension contribution is plausible.")

    def validate_tax(self, total_gross_salary: Decimal):
        stated_qst = self.get_aggregate_val_sum(known_tax_deduction_entry)
        if not self.employee.withholding_tax:
            if stated_qst != Decimal(0):
                print_fail(
                    "Withholding tax found in payslip with no expected withholding tax."
                )
            return
        # compute the expected withholding tax (first row after the total row)
        subtotal_df = self.get_subtotal_slice(known_tax_deduction_entry)
        if subtotal_df is None:
            print_fail("No subtotals found for withholding tax.")
            return
        # TODO: update this heuristic with regex or something more reliable
        if not str(subtotal_df.index[0]).find("SI-Days"):
            print_fail(
                "Unrecognized withholding tax subtotal format. Please report this issue."
            )
            return
        # parse the relevant subtotal row name
        # e.g. January 2022 / 30 SI-Days / ZH / A0N
        subtotal_tokens = str(subtotal_df.index[0]).split("/")
        canton_code = subtotal_tokens[2].strip()
        qst_code = subtotal_tokens[3].strip()
        # perform some basic validation
        if not qst.is_qst_code_supported(qst_code):
            print_warn(
                f"Tax class {qst_code} is not supported by this tool. Skipping validation."
            )
            return
        if qst.has_annual_qst_model(canton_code):
            print_warn(
                f"Canton {canton_code} has an annual withholding tax model. This check will likely erroneously fail."
            )
        if not subtotal_tokens[1].strip() == "30 SI-Days":
            print_warn(
                f"Your payslip does not have 30 SI-Days. This check will likely erroneously fail."
            )
        # calculate the expected withholding tax
        expected_qst = -qst.calculate_withholding_tax(
            self.payslip_date.year,
            canton_code,
            qst_code,
            total_gross_salary,
            allow_annual_model=True,
        )
        # output manual validation information
        print_note(
            f"Ensure that the following tax class is correct:\n{qst.explain_qst_code(qst_code)}"
        )
        # validate the withholding tax
        Payslip.validate_calc("Withholding tax", stated_qst, expected_qst)

    def validate_espp_contribution(self, monthly_base_salary: Decimal):
        if self.row_exists("ESPP"):
            stated_espp_contrib = self.get_val("ESPP")
            stated_espp_rate = self.get_val("ESPP", "Rate")
            applicable_salary = monthly_base_salary + self.get_val("Bonus")
            expected_espp_contrib = -round_05(
                applicable_salary * (stated_espp_rate / Decimal(100))
            )
            Payslip.validate_calc(
                "ESPP contribution",
                stated_espp_contrib,
                expected_espp_contrib,
            )

    def validate_wage_paid(self):
        print_note(
            'Validation of "Wage paid" is not yet implemented as it requires recomputation of the payslip.'
        )
        print_note(
            'Any other reported errors will likely affect the correctness of "Wage paid."'
        )

    def validate_balance_forward(self):
        # check if the aggregate sum of balance forward is not zero
        balance_forward_aggregate_sum = self.get_aggregate_val_sum("Balance forward")
        if balance_forward_aggregate_sum != Decimal(0):
            print_fail(
                f"Aggregate sum of balance forward is not zero: {balance_forward_aggregate_sum}"
            )
        # check for indicators of missing supplementary payslips
        if self.get_val("Balance forward"):
            if not any(other.get_val("Balance forward") for other in self.supplements):
                print_fail(
                    '"Balance forward" row found but no associated supplementary payslip specified.'
                )
                print_warn(
                    "Subsequent results may be erroneous with potentially missing supplementary payslips."
                )

    def validate(self):
        # validate balance forward
        self.validate_balance_forward()
        # validate the base salary against the expected base salary
        monthly_base_salary = self.validate_monthly_base_salary(
            self.employee.base_salary  # None is handled in validate_monthly_base_salary
        )
        annual_base_salary = (
            self.employee.base_salary
            if self.employee.base_salary
            else monthly_base_salary * Decimal(12)
        )

        # validate supplementary payslips
        for supplement in self.supplements:
            supplement.validate()

        stated_gross_salary = self.get_val("Gross salary")
        (
            gross_salary,
            gross_salary_si,
            gross_salary_non_cash,
        ) = self.validate_gross_salary()

        self.validate_net_salary_calculations(gross_salary, gross_salary_si)

        self.validate_oasi_contribution(gross_salary_si)
        self.validate_ui_contribution(gross_salary_si)
        self.validate_suva_contributions(gross_salary_si)
        self.validate_dsa_contributions(gross_salary_si)
        self.validate_bvg_contributions(annual_base_salary)
        self.validate_tax(gross_salary)

        self.validate_espp_contribution(monthly_base_salary)
        self.validate_wage_paid()


# usage: verify.py [-h] [-y BIRTH_YEAR] [-b BASE] [-p PENSION_CONTRIBUTION] [-w] [-s STOCK_PAYSLIP_PATH] payslip_path
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "payslip_path", type=Path, help="Path to primary input payslip to verify."
    )
    parser.add_argument(
        "-y",
        "--birth_year",
        type=str,
        required=True,
        help="Year of birth in YYYY format.",
    )
    parser.add_argument(
        "-b", "--base-salary", type=Decimal, help="Annual base salary in CHF."
    )
    parser.add_argument(
        "-p",
        "--pension_contribution",
        type=Decimal,
        default=None,
        help="Monthly BVG (Pensionskasse) employee contribution, if obtainable from the certificate of insurance (Vorsorgeausweis).",
    )
    parser.add_argument(
        "-w",
        "--withholding_tax",
        action="store_true",
        help="Specify if subject to tax withholding at source (Quellensteuerpflichtig).",
    )
    parser.add_argument(
        "-s",
        "--stock_payslip_path",
        default=None,
        type=Path,
        help="Path to a stock grant payslip supplementing the primary payslip.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    # parse employee info
    employee = EmployeeData(
        datetime.datetime.strptime(args.birth_year, "%Y").date().year,
        args.withholding_tax,
        args.base_salary,
        args.pension_contribution,
    )
    # parse stock payslip if one is provided
    # note: multiple supplementary payslips are supported but don't seem to occur in practice
    supplements = (
        [SupplementaryPayslip(args.stock_payslip_path, employee)]
        if args.stock_payslip_path
        else []
    )
    wage_payslip = WagePayslip(args.payslip_path, employee, supplements)
    wage_payslip.validate()


if __name__ == "__main__":
    main()
