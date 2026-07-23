"""Strategy template extensions for shared application services."""

from ...template import CtaTemplate

from ..mysql_data import MysqlDataService


class CtaTemplateService(CtaTemplate):
    """CTA template that exposes optional shared strategy services."""

    mysql_data_service_name: str = "default"

    def get_mysql_data_service(self) -> MysqlDataService:
        """Return the process-wide MySQL service used by this strategy."""
        return MysqlDataService.get_shared(self.mysql_data_service_name)
