import secrets
import string


def generate_password(length=12):
    alphabet = (
        string.ascii_letters
        + string.digits
        + string.punctuation
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


password = generate_password(12)
print(password)
