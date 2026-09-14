"""
Tabelas do Revenue Scan.

Tudo aqui é dado público do DATASUS (`ClasseDado.PUBLICO`): serve a qualquer
tenant que contratou o produto, dentro do escopo do contrato. Dado interno de
hospital, quando entrar, fica em tabelas próprias com `tenant_id` obrigatório.

As tabelas não declaram schema: no Postgres a conexão usa `search_path` no
schema do serviço (`DB_SCHEMA`), e os testes rodam em SQLite sem schema.

`competencia` é sempre o mês de PROCESSAMENTO (o do nome do arquivo, AAAAMM).
O mês de atendimento da AIH é outro campo, `competencia_aih`: uma AIH de março
pode ser processada — e rejeitada — em maio.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.adapters.base import ClasseDado

# BigInteger no Postgres (a tabela de AIH aprovadas passa de milhões de linhas
# com várias UFs); Integer no SQLite, que só gera id automático para INTEGER.
_ID = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


class _Publico:
    classe_dado = ClasseDado.PUBLICO


class DataLoad(_Publico, Base):
    """Uma tentativa de carga de uma fonte, com o arquivo e o resultado."""

    __tablename__ = "data_loads"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    # SIH_RD | SIH_RJ | SIH_ER | SIH_ERROS | CNES_API
    fonte: Mapped[str] = mapped_column(String(20), nullable=False)
    uf: Mapped[str | None] = mapped_column(String(2))
    competencia: Mapped[str | None] = mapped_column(String(6))
    # DOWNLOAD | UPLOAD | API
    origem: Mapped[str] = mapped_column(String(10), nullable=False)
    arquivo: Mapped[str | None] = mapped_column(String(255))
    checksum: Mapped[str | None] = mapped_column(String(64))
    # RUNNING | OK | FAILED
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="RUNNING")
    linhas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erro: Mapped[str | None] = mapped_column(Text)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_data_loads_fonte_uf_competencia", "fonte", "uf", "competencia"),)


class Establishment(_Publico, Base):
    """Estabelecimento de saúde pelo CNES, com o nome que o hospital usa."""

    __tablename__ = "establishments"

    cnes: Mapped[str] = mapped_column(String(7), primary_key=True)
    nome_fantasia: Mapped[str | None] = mapped_column(String(255))
    razao_social: Mapped[str | None] = mapped_column(String(255))
    uf: Mapped[str | None] = mapped_column(String(2))
    codigo_municipio: Mapped[str | None] = mapped_column(String(7))
    # CNPJ da entidade no CNES. Hospital estadual gerido por OSS aparece com o
    # CNPJ da secretaria, não o da OSS — por isso o vínculo com a OSS é tabela
    # própria, com fonte.
    cnpj_entidade: Mapped[str | None] = mapped_column(String(14))
    natureza_juridica: Mapped[str | None] = mapped_column(String(4))
    esfera: Mapped[str | None] = mapped_column(String(20))
    tipo_unidade: Mapped[int | None] = mapped_column(Integer)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ManagementOrganization(_Publico, Base):
    """Organização Social de Saúde ou outra gestora de vários hospitais."""

    __tablename__ = "management_organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sigla: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    cnpj: Mapped[str | None] = mapped_column(String(14))
    uf: Mapped[str | None] = mapped_column(String(2))
    site: Mapped[str | None] = mapped_column(String(255))
    fonte: Mapped[str | None] = mapped_column(String(500))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    unidades: Mapped[list[OrganizationEstablishment]] = relationship(
        back_populates="organizacao", cascade="all, delete-orphan", order_by="OrganizationEstablishment.cnes"
    )


class OrganizationEstablishment(_Publico, Base):
    """
    Hospital gerido por uma organização, com a fonte que diz isso.

    CONFIRMADO só quando a fonte foi conferida; o scan de uma OSS com vínculo
    A_CONFIRMAR mostra a ressalva.
    """

    __tablename__ = "organization_establishments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("management_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    sigla: Mapped[str | None] = mapped_column(String(20))
    # CONFIRMADO | A_CONFIRMAR
    situacao: Mapped[str] = mapped_column(String(12), nullable=False, default="A_CONFIRMAR")
    fonte: Mapped[str | None] = mapped_column(String(500))
    verificado_em: Mapped[date | None] = mapped_column(Date)

    organizacao: Mapped[ManagementOrganization] = relationship(back_populates="unidades")

    __table_args__ = (UniqueConstraint("organization_id", "cnes", name="uq_organization_establishments"),)


class SihHospitalMonth(_Publico, Base):
    """Produção e rejeição do hospital num mês de processamento do SIH."""

    __tablename__ = "sih_hospital_month"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    aih_aprovadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valor_aprovado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    aih_rejeitadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valor_rejeitado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    diarias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    diarias_uti: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    permanencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("uf", "cnes", "competencia", name="uq_sih_hospital_month"),
        Index("ix_sih_hospital_month_cnes", "cnes", "competencia"),
    )


class SihApprovedAih(_Publico, Base):
    """
    Número da AIH aprovada em cada processamento.

    Só o número: é o que responde se uma AIH rejeitada voltou aprovada depois,
    em qualquer ordem de carga.
    """

    __tablename__ = "sih_approved_aih"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    n_aih: Mapped[str] = mapped_column(String(13), nullable=False)

    __table_args__ = (
        UniqueConstraint("uf", "n_aih", "competencia", name="uq_sih_approved_aih"),
        Index("ix_sih_approved_aih_n_aih", "n_aih"),
        Index("ix_sih_approved_aih_uf_competencia", "uf", "competencia"),
    )


class SihRejection(_Publico, Base):
    """AIH rejeitada num processamento (arquivo RJ)."""

    __tablename__ = "sih_rejections"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    n_aih: Mapped[str] = mapped_column(String(13), nullable=False)
    competencia_aih: Mapped[str | None] = mapped_column(String(6))
    proc_realizado: Mapped[str | None] = mapped_column(String(10))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    dt_internacao: Mapped[date | None] = mapped_column(Date)
    dt_saida: Mapped[date | None] = mapped_column(Date)
    marca_uti: Mapped[str | None] = mapped_column(String(2))

    __table_args__ = (
        UniqueConstraint("uf", "n_aih", "competencia", name="uq_sih_rejections"),
        Index("ix_sih_rejections_cnes_competencia", "cnes", "competencia"),
        Index("ix_sih_rejections_uf_competencia", "uf", "competencia"),
    )


class SihRejectionReason(_Publico, Base):
    """Motivo oficial da rejeição (arquivo ER). Uma AIH pode ter vários."""

    __tablename__ = "sih_rejection_reasons"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    n_aih: Mapped[str] = mapped_column(String(13), nullable=False)
    codigo_erro: Mapped[str] = mapped_column(String(6), nullable=False)

    __table_args__ = (
        UniqueConstraint("uf", "n_aih", "competencia", "codigo_erro", name="uq_sih_rejection_reasons"),
        Index("ix_sih_rejection_reasons_cnes_competencia", "cnes", "competencia"),
        Index("ix_sih_rejection_reasons_uf_competencia", "uf", "competencia"),
    )


class SihErrorCode(_Publico, Base):
    """Descrição dos motivos de rejeição (tabela auxiliar do SIH, TAB 0027)."""

    __tablename__ = "sih_error_codes"

    codigo: Mapped[str] = mapped_column(String(6), primary_key=True)
    descricao: Mapped[str] = mapped_column(String(255), nullable=False)
