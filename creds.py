# -*- coding: utf-8 -*-
"""
เก็บรหัสผ่านของแต่ละบริษัท/แต่ละเว็บไว้ใน Windows Credential Manager (ผ่านไลบรารี keyring)
- รหัสผ่านไม่ถูกเขียนลงไฟล์ของโปรแกรม (config.yaml เก็บแค่ชื่อผู้ใช้)
- ดู/ลบเองได้ที่ Windows: Control Panel → Credential Manager → Windows Credentials → "TaxDocs"
"""
SERVICE = "TaxDocs"


def _kr():
    try:
        import keyring
        from keyring.backends import fail
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def available():
    return _kr() is not None


def _key(tax_id, portal):
    return f"{tax_id}:{portal}"


def set_password(tax_id, portal, password):
    kr = _kr()
    if not kr:
        raise RuntimeError("เครื่องนี้ยังเก็บรหัสผ่านไม่ได้ (ต้องติดตั้งไลบรารี keyring บน Windows)")
    kr.set_password(SERVICE, _key(tax_id, portal), password)


def get_password(tax_id, portal):
    kr = _kr()
    if not kr:
        return None
    try:
        return kr.get_password(SERVICE, _key(tax_id, portal))
    except Exception:
        return None


def has_password(tax_id, portal):
    return bool(get_password(tax_id, portal))


def delete_password(tax_id, portal):
    kr = _kr()
    if not kr:
        return
    try:
        kr.delete_password(SERVICE, _key(tax_id, portal))
    except Exception:
        pass
