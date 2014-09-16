from tornado_proxy.proxy import ProxyHandler  # noqa


def run_proxy(port, cache=None, debug=False, start_ioloop=True):
    """
    Run proxy on the specified port. If start_ioloop is True (default),
    the tornado IOLoop will be started immediately.
    """
    if debug:
        from tornado.log import enable_pretty_logging
        enable_pretty_logging()
    import tornado.web
    handlers = [
        (r'.*', ProxyHandler, {'cache': cache}),
    ]
    if cache is not None:
        from tornado_proxy.cache import CacheHandler, CacheListHandler
        handlers.insert(0, (r'^/cache/list/$', CacheListHandler, {'cache': cache}))
        handlers.insert(0, (r'^/cache/$', CacheHandler, {'cache': cache}))
    app = tornado.web.Application(handlers, debug=debug)
    app.listen(port)
    ioloop = tornado.ioloop.IOLoop.instance()

    if start_ioloop:
        ioloop.start()
