def classFactory(iface):
    from .vorflow_plugin import VorflowPlugin
    return VorflowPlugin(iface)
