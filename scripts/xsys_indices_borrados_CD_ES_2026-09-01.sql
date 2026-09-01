/* Indices REALES de CD_ES borrados el 01/09/2026 por falta de uso.
   Uso medido en 33 dias (motor arrancado el 30/07/2026).
   Para reponer cualquiera de los dos, ejecutar su CREATE. */

/* IX_CDES_PorTipoCont -- seeks=0 scans=9 lookups=0 updates=110002 */
CREATE NONCLUSTERED INDEX [IX_CDES_PorTipoCont] ON [dbo].[CD_ES] ([Tipo], [Id_Controlador], [Id_ES]) WITH (PAD_INDEX = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO

/* IX_CDES_CD_Motivos -- seeks=5 scans=0 lookups=0 updates=110002 */
CREATE NONCLUSTERED INDEX [IX_CDES_CD_Motivos] ON [dbo].[CD_ES] ([Id_CD_Motivo]) WITH (PAD_INDEX = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO

