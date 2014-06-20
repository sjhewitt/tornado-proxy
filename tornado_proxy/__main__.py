

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Run a Tornado based proxy.')
    parser.add_argument('--port', dest='port', type=int, default=8888,
                        help='the port to listen on')
    parser.add_argument('--debug', dest='debug', action='store_true',
                        default=False, help='Run in debug mode')
    parser.add_argument('--cache', dest='cache',
                        help='the type of cache to use',
                        choices=['simple', 'file', 'wayback'])
    parser.add_argument('--cache-folder', dest='cache_folder',
                        help='the folder to store cache files in (default: '
                        '/tmp/proxy_cache)',
                        default='/tmp/proxy_cache')
    args = parser.parse_args()

    if args.cache == 'wayback':
        from tornado_proxy.cache import WaybackFileSystemCache
        cache = WaybackFileSystemCache("/tmp/proxy_cache")
    elif args.cache == 'file':
        from tornado_proxy.cache import FileSystemCache
        cache = FileSystemCache("/tmp/proxy_cache")
    elif args.cache == 'simple':
        from tornado_proxy.cache import SimpleCache
        cache = SimpleCache()
    else:
        cache = None

    from tornado_proxy import run_proxy
    print ("Starting HTTP proxy on port %d" % args.port)
    run_proxy(args.port, cache=cache, debug=args.debug)
