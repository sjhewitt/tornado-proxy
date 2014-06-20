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
    app = tornado.web.Application([
        (r'.*', ProxyHandler, {'cache': cache}),
    ], debug=debug)
    app.listen(port)
    ioloop = tornado.ioloop.IOLoop.instance()

    if start_ioloop:
        ioloop.start()
