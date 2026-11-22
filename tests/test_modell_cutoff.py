import modell_cutoff as mc


def test_hy3_sav():
    assert mc.tisztasag("tencent/Hy3", "2025-03") == "CONTAMINATED"
    assert mc.tisztasag("tencent/Hy3", "2025-06") == "GRAY"
    assert mc.tisztasag("tencent/Hy3", "2026-04") == "CLEAN"
    assert mc.tisztasag("ismeretlen/modell", "2026-04") == "ISMERETLEN"


def test_a_futas_a_leggyengebb_lepese():
    assert mc.futas_tisztasaga(["tencent/Hy3", "ismeretlen/modell"], "2026-04") == "ISMERETLEN"
    assert mc.futas_tisztasaga(["tencent/Hy3"], "2026-04") == "CLEAN"


def test_a_11_22_i_szonda_sorai():
    for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "deepseek-ai/DeepSeek-V4.1-Flash"):
        c = mc.CUTOFF[m]
        assert c["cutoff"] < c["tiszta_tol"] and "cutoff_szonda_20261122" in c["forras"]
        assert mc.tisztasag(m, "2026-04") == "CLEAN"          # a HU-választás egyik modellnél sem szennyezett
    assert mc.tisztasag("claude-opus-5-5", "2026-01") == "CONTAMINATED"
    assert mc.tisztasag("claude-opus-5-5", "2026-02") == "GRAY"   # a februári sziget
    assert mc.futas_tisztasaga(["claude-sonnet-5-5", "tencent/Hy3"], "2026-01") == "GRAY"
