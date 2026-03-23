#!/usr/bin/env python3
"""Creates sample input Excel files for testing."""
import os
import openpyxl

os.makedirs("input", exist_ok=True)

# Sample input file 1
wb1 = openpyxl.Workbook()
ws1 = wb1.active
ws1.title = "Parts"
ws1.append(["sku_raw", "brand_hint", "qty_needed", "comment"])
ws1.append(["A9616710710", "Mercedes", 1, "тест Autopiter"])
ws1.append(["QO600005", "Q-FILTER", 2, "тест Armtek"])
ws1.append(["2486655", "", 1, "тест Tsmavto pcode"])
wb1.save("input/sample_parts_1.xlsx")
print("Created input/sample_parts_1.xlsx")

# Sample input file 2 (with a duplicate SKU to test dedup)
wb2 = openpyxl.Workbook()
ws2 = wb2.active
ws2.title = "Parts2"
ws2.append(["Артикул", "Бренд", "Комментарий"])
ws2.append(["A9616710710", "Mercedes-Benz", "дубликат — не должен дублироваться в очереди"])
ws2.append(["3501220-01", "FOTON", "тест Truckdrive"])
wb2.save("input/sample_parts_2.xlsx")
print("Created input/sample_parts_2.xlsx")

print("Sample input files created in ./input/")
