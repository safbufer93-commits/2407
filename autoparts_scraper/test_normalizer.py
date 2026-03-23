#!/usr/bin/env python3
"""Unit tests for normalizer module."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.normalizer import normalize_sku, normalize_price, normalize_availability, normalize_brand, is_valid_sku

def test_sku():
    assert normalize_sku("a9616710710") == "A9616710710"
    assert normalize_sku(" QO600005 ") == "QO600005"
    assert normalize_sku("A961 671 0710") == "A9616710710"
    assert normalize_sku("3501220-01") == "3501220-01"
    assert not is_valid_sku("Артикул")  # Russian letters → invalid
    assert is_valid_sku("A9616710710")
    print("  SKU tests: PASS")

def test_price():
    assert normalize_price("136 171 ₽") == 136171.0
    assert normalize_price("от 135 295 ₽") == 135295.0
    assert normalize_price("4\u00a0436\u00a0₽") == 4436.0
    assert normalize_price("") is None
    assert normalize_price(None) is None
    assert normalize_price("1 234 567") == 1234567.0
    assert normalize_price("нет цены") is None
    print("  Price tests: PASS")

def test_availability():
    assert normalize_availability("64 шт") == "QTY=64"
    assert normalize_availability("1 шт") == "QTY=1"
    assert normalize_availability("В наличии") == "IN_STOCK"
    assert normalize_availability("есть") == "IN_STOCK"
    assert normalize_availability("нет в наличии") == "OUT_OF_STOCK"
    assert normalize_availability("под заказ") == "PREORDER"
    assert normalize_availability("REQUIRES_PHONE") == "REQUIRES_PHONE"
    assert normalize_availability("") == "UNKNOWN"
    print("  Availability tests: PASS")

def test_brand():
    assert normalize_brand("mercedes-benz") == "MERCEDES"
    assert normalize_brand("Mercedes") == "MERCEDES"
    assert normalize_brand("MERCEDES BENZ") == "MERCEDES"
    assert normalize_brand("mb") == "MERCEDES"
    assert normalize_brand("Q-FILTER") == "Q-FILTER"
    assert normalize_brand("") == ""
    print("  Brand tests: PASS")

if __name__ == "__main__":
    print("Running normalizer tests...")
    test_sku()
    test_price()
    test_availability()
    test_brand()
    print("\nAll tests PASSED!")
