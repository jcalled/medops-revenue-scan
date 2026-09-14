"""
Ambiente de teste: nada de banco real nem núcleo real.

As variáveis são definidas antes de importar o app, porque a configuração é
lida uma vez.
"""
import os

os.environ.update({
    "APP_ENV": "test",
    "JWT_SECRET": "segredo-de-teste-com-mais-de-trinta-e-dois-caracteres",
    "CORE_API_URL": "http://nucleo.test",
    "DATABASE_URL": "sqlite://",
    "CORS_ORIGINS": "http://localhost:3000",
})
