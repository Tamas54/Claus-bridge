import modell_cutoff as mc


def test_hy3_sav():
    assert mc.tisztasag("tencent/Hy3", "2025-03") == "CONTAMINATED"
    assert mc.tisztasag("tencent/Hy3", "2025-06") == "GRAY"
    assert mc.tisztasag("tencent/Hy3", "2026-04") == "CLEAN"
    assert mc.tisztasag("ismeretlen/modell", "2026-04") == "ISMERETLEN"


def test_a_futas_a_leggyengebb_lepese():
    assert mc.futas_tisztasaga(["tencent/Hy3", "ismeretlen/modell"], "2026-04") == "ISMERETLEN"
    assert mc.futas_tisztasaga(["tencent/Hy3"], "2026-04") == "CLEAN"
