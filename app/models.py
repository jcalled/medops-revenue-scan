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

from typing import Any

from sqlalchemy import (
    JSON, BigInteger, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.adapters.base import ClasseDado

# BigInteger no Postgres (a tabela de AIH aprovadas passa de milhões de linhas
# com várias UFs); Integer no SQLite, que só gera id automático para INTEGER.
_ID = BigInteger().with_variant(Integer(), "sqlite")
_JSON = JSON().with_variant(JSONB(), "postgresql")


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

    O número responde se uma AIH rejeitada voltou aprovada depois, em qualquer
    ordem de carga; o valor é o que a recuperação cobra.
    """

    __tablename__ = "sih_approved_aih"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    n_aih: Mapped[str] = mapped_column(String(13), nullable=False)
    # VAL_TOT do RD: o dinheiro que entrou. É a base da cobrança da recuperação.
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

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


class SihHospitalProcedureMonth(_Publico, Base):
    """
    Produção aprovada por procedimento, hospital e mês.

    É o que permite comparar hospital com hospital de igual para igual: o valor
    médio e a permanência esperados de um hospital saem do mix de
    procedimentos DELE, com a média dos semelhantes em cada procedimento.
    """

    __tablename__ = "sih_hospital_procedure_month"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    proc_realizado: Mapped[str] = mapped_column(String(10), nullable=False)
    # 02 média, 03 alta; vazio quando o arquivo não informa.
    complexidade: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    aih: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valor: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    diarias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    diarias_uti: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    permanencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("uf", "cnes", "competencia", "proc_realizado", "complexidade",
                         name="uq_sih_hospital_procedure_month"),
        Index("ix_sih_hospital_procedure_month_cnes", "cnes", "competencia"),
        Index("ix_sih_hospital_procedure_month_proc", "proc_realizado", "competencia"),
    )


class CnesBed(_Publico, Base):
    """Leitos do estabelecimento por código e tipo (arquivo LT do CNES)."""

    __tablename__ = "cnes_beds"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    codigo_leito: Mapped[str] = mapped_column(String(2), nullable=False)
    # 1 cirúrgico, 2 clínico, 3 complementar (UTI/UCI), 4 obstétrico, 5 pediátrico,
    # 6 outras especialidades, 7 hospital-dia.
    tipo_leito: Mapped[str] = mapped_column(String(1), nullable=False)
    qt_existente: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qt_sus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("uf", "competencia", "cnes", "codigo_leito", "tipo_leito", name="uq_cnes_beds"),
        Index("ix_cnes_beds_cnes", "cnes", "competencia"),
    )


class CnesEnablement(_Publico, Base):
    """Habilitação do estabelecimento, com vigência (arquivo HB do CNES)."""

    __tablename__ = "cnes_enablements"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    competencia: Mapped[str] = mapped_column(String(6), nullable=False)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    habilitacao: Mapped[str] = mapped_column(String(4), nullable=False)
    competencia_inicio: Mapped[str] = mapped_column(String(6), nullable=False, default="")
    competencia_fim: Mapped[str | None] = mapped_column(String(6))

    __table_args__ = (
        UniqueConstraint("uf", "competencia", "cnes", "habilitacao", "competencia_inicio", name="uq_cnes_enablements"),
        Index("ix_cnes_enablements_cnes", "cnes", "competencia"),
    )


class PeerGroup(_Publico, Base):
    """Hospitais semelhantes a um hospital num período, e por que foram escolhidos."""

    __tablename__ = "peer_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo_inicio: Mapped[str] = mapped_column(String(6), nullable=False)
    periodo_fim: Mapped[str] = mapped_column(String(6), nullable=False)
    # Filtros aplicados, alargamentos feitos e os atributos do hospital.
    criterio: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    membros: Mapped[list[PeerGroupMember]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan", order_by="PeerGroupMember.distancia")
    metricas: Mapped[list[BenchmarkMetric]] = relationship(back_populates="grupo", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("cnes", "periodo_inicio", "periodo_fim", name="uq_peer_groups"),)


class PeerGroupMember(_Publico, Base):
    __tablename__ = "peer_group_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_group_id: Mapped[int] = mapped_column(ForeignKey("peer_groups.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    distancia: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    grupo: Mapped[PeerGroup] = relationship(back_populates="membros")


class BenchmarkMetric(_Publico, Base):
    """Um indicador do hospital contra os semelhantes: valor, mediana, quartis e percentil."""

    __tablename__ = "benchmark_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_group_id: Mapped[int] = mapped_column(ForeignKey("peer_groups.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    metrica: Mapped[str] = mapped_column(String(40), nullable=False)
    valor: Mapped[float | None] = mapped_column(Float)
    mediana: Mapped[float | None] = mapped_column(Float)
    p25: Mapped[float | None] = mapped_column(Float)
    p75: Mapped[float | None] = mapped_column(Float)
    # Posição do hospital entre os semelhantes, 0 a 100 (100 = maior valor).
    percentil: Mapped[float | None] = mapped_column(Float)
    n_pares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    grupo: Mapped[PeerGroup] = relationship(back_populates="metricas")

    __table_args__ = (UniqueConstraint("peer_group_id", "metrica", name="uq_benchmark_metrics"),)


class Opportunity(_Publico, Base):
    """
    Oportunidade detectada pelo motor, com a prova.

    `status` começa em ESTIMATED_OPPORTUNITY. Rejeição registrada no ER entra
    como CONFIRMED: o SUS registrou a perda. IN_RECOVERY e RECOVERED dependem do
    trabalho do hospital e ficam no acompanhamento do tenant, não aqui.
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo_inicio: Mapped[str] = mapped_column(String(6), nullable=False)
    periodo_fim: Mapped[str] = mapped_column(String(6), nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    categoria: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    unidade: Mapped[str] = mapped_column(String(12), nullable=False, default="R$")
    observed_value: Mapped[float | None] = mapped_column(Float)
    benchmark_value: Mapped[float | None] = mapped_column(Float)
    gap: Mapped[float | None] = mapped_column(Float)
    estimated_financial_impact: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_opportunities_cnes_periodo", "cnes", "periodo_inicio", "periodo_fim"),
        Index("ix_opportunities_tipo", "opportunity_type"),
    )


class HospitalScore(_Publico, Base):
    """Revenue Opportunity Score do hospital no período, para ranquear sem recalcular."""

    __tablename__ = "hospital_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo_inicio: Mapped[str] = mapped_column(String(6), nullable=False)
    periodo_fim: Mapped[str] = mapped_column(String(6), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impacto_estimado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    valor_apresentado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    principal_tipo: Mapped[str | None] = mapped_column(String(40))
    principal_categoria: Mapped[str | None] = mapped_column(String(40))
    # Atributos do hospital no cálculo, para filtrar e ordenar sem recalcular.
    impacto_confirmado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    impacto_sinais: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    uf: Mapped[str | None] = mapped_column(String(2))
    codigo_municipio: Mapped[str | None] = mapped_column(String(7))
    natureza_codigo: Mapped[str | None] = mapped_column(String(4))
    natureza_grupo: Mapped[str | None] = mapped_column(String(20))
    gestao: Mapped[str | None] = mapped_column(String(20))
    porte: Mapped[str | None] = mapped_column(String(30))
    leitos_sus: Mapped[int | None] = mapped_column(Integer)
    gerado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("cnes", "periodo_inicio", "periodo_fim", name="uq_hospital_scores"),
        Index("ix_hospital_scores_uf_natureza", "uf", "natureza_grupo"),
    )


class Municipality(_Publico, Base):
    """Município do IBGE. O CNES e o SIH usam os 6 primeiros dígitos do código."""

    __tablename__ = "municipalities"

    codigo: Mapped[str] = mapped_column(String(7), primary_key=True)
    codigo_cnes: Mapped[str] = mapped_column(String(6), nullable=False, unique=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)


class _Privado:
    classe_dado = ClasseDado.PRIVADO


class RecoveryTracking(_Privado, Base):
    """
    Acompanhamento da recuperação contratada: hospitais, mês de início e as
    condições do modelo híbrido. É do tenant (ou da MedOps, antes do contrato,
    com tenant_id vazio) e nunca aparece para outro.
    """

    __tablename__ = "recovery_trackings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("management_organizations.id", ondelete="SET NULL"))
    cnes: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    # Primeiro mês de PROCESSAMENTO do contrato (AAAAMM).
    inicio: Mapped[str] = mapped_column(String(6), nullable=False)
    percentual: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=15)
    fixo_por_hospital: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=6900)
    # ATIVO | ENCERRADO
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ATIVO")
    criado_por: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    conferido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # O que os hospitais já recuperavam sozinhos por mês antes do contrato, por CNES.
    # Só o que passa disso entra no percentual. CALCULADA pelos meses anteriores
    # ao início ou NEGOCIADA com a organização.
    linha_de_base: Mapped[dict[str, float]] = mapped_column(_JSON, nullable=False, default=dict)
    linha_de_base_meses: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    linha_de_base_origem: Mapped[str] = mapped_column(String(12), nullable=False, default="CALCULADA")

    itens: Mapped[list[RecoveryItem]] = relationship(
        back_populates="acompanhamento", cascade="all, delete-orphan", order_by="RecoveryItem.id"
    )


class RecoveryItem(_Privado, Base):
    """
    AIH marcada para recuperar num acompanhamento.

    BASE: rejeitada antes do início e ainda não recebida. NOVA: rejeitada
    durante o acompanhamento. Vira RECUPERADA quando aparece aprovada num
    processamento posterior à rejeição, com o mês e o valor aprovado.
    """

    __tablename__ = "recovery_items"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    tracking_id: Mapped[int] = mapped_column(
        ForeignKey("recovery_trackings.id", ondelete="CASCADE"), nullable=False, index=True)
    cnes: Mapped[str] = mapped_column(String(7), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    n_aih: Mapped[str] = mapped_column(String(13), nullable=False)
    origem: Mapped[str] = mapped_column(String(8), nullable=False)
    competencia_rejeicao: Mapped[str] = mapped_column(String(6), nullable=False)
    competencia_aih: Mapped[str | None] = mapped_column(String(6))
    procedimento: Mapped[str | None] = mapped_column(String(10))
    dt_saida: Mapped[date | None] = mapped_column(Date)
    valor_rejeitado: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    categoria: Mapped[str] = mapped_column(String(30), nullable=False)
    motivos: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    # EM_ABERTO | RECUPERADA
    situacao: Mapped[str] = mapped_column(String(12), nullable=False, default="EM_ABERTO")
    competencia_aprovacao: Mapped[str | None] = mapped_column(String(6))
    valor_aprovado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    marcado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    recuperado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    acompanhamento: Mapped[RecoveryTracking] = relationship(back_populates="itens")

    __table_args__ = (
        UniqueConstraint("tracking_id", "n_aih", name="uq_recovery_items"),
        Index("ix_recovery_items_tracking_situacao", "tracking_id", "situacao"),
    )


class Prospect(_Privado, Base):
    """
    Organização em prospecção: dados públicos da pesquisa (planilha) e o
    andamento comercial. É da MedOps; nenhum tenant vê.
    """

    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("management_organizations.id", ondelete="SET NULL"), index=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    uf: Mapped[str | None] = mapped_column(String(2))
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int | None] = mapped_column(Integer)
    prioridade: Mapped[str | None] = mapped_column(String(4))
    presenca: Mapped[str | None] = mapped_column(String(255))
    situacao_escopo: Mapped[str | None] = mapped_column(String(255))
    hospitais_confirmados: Mapped[int | None] = mapped_column(Integer)
    rede: Mapped[str | None] = mapped_column(Text)
    principais_unidades: Mapped[str | None] = mapped_column(Text)
    lideranca: Mapped[str | None] = mapped_column(String(255))
    contato_publico: Mapped[str | None] = mapped_column(Text)
    site: Mapped[str | None] = mapped_column(String(255))
    fonte: Mapped[str | None] = mapped_column(String(500))
    fit: Mapped[str | None] = mapped_column(String(255))
    confianca: Mapped[str | None] = mapped_column(String(20))
    # MAPEADA | CONTATO | REUNIAO | DIAGNOSTICO | PROPOSTA | NEGOCIACAO | FECHADA | PERDIDA
    etapa: Mapped[str] = mapped_column(String(20), nullable=False, default="MAPEADA")
    contato_nome: Mapped[str | None] = mapped_column(String(120))
    contato_cargo: Mapped[str | None] = mapped_column(String(120))
    contato_email: Mapped[str | None] = mapped_column(String(255))
    contato_telefone: Mapped[str | None] = mapped_column(String(60))
    responsavel: Mapped[str | None] = mapped_column(String(120))
    proxima_acao: Mapped[str | None] = mapped_column(Text)
    proxima_acao_em: Mapped[date | None] = mapped_column(Date)
    proposta_percentual: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    proposta_fixo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    motivo_perda: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    eventos: Mapped[list[ProspectEvent]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan", order_by="ProspectEvent.id.desc()")


class ProspectEvent(_Privado, Base):
    """Histórico da prospecção: nota, mudança de etapa, proposta, importação."""

    __tablename__ = "prospect_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    # NOTA | ETAPA | PROPOSTA | IMPORTACAO
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    criado_por: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    prospect: Mapped[Prospect] = relationship(back_populates="eventos")
