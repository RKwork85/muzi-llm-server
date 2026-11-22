"""验证码存储。按渠道（sms / email）区分 namespace。"""

from app.verification.codes import VerificationCodeStore, code_digest, new_code

__all__ = ["VerificationCodeStore", "code_digest", "new_code"]
