"""Stats query endpoint."""

from ..stats import StatsCollector


def register(app, stats: StatsCollector):

    @app.get("/stats")
    async def get_stats(agent: str | None = None, hours: float = 24):
        return stats.query(agent=agent, hours=hours)
