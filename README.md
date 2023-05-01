# Switzerland Payslip Validation Tool :construction:
This tool is a work-in-progress personal project created to understand and attempt to validate payslips in Switzerland. It is NOT an accounting tool, and it does not cover a wide range of payroll and accounting scenarios. Thus, do not assume that the results this tool produces are correct or accurate.

The material embodied in this repository and software is provided to you "as-is" and without warranty of any kind, express, implied or otherwise, including without limitation, any warranty of fitness for a particular purpose.

## Example Usage:
```
python verify.py test-dec.pdf --birth_year=1990 --withholding_tax --pension_contribution=1234.56 --base-salary=123456.78 --stock_payslip_path=test-dec-stock.pdf
```
