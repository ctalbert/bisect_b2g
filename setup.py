from setuptools import setup
setup(
    name="bisect-b2g",
    version="1",
    packages=['bisect_b2g'],
    entry_points={
        'console_scripts': [
            'bisect = bisect_b2g.driver:main'
        ]
    },
    install_requires=["isodate", "mako", "GitPython>=0.3", "hgapi", "mozprocess"],
    tests_require=["pytest", "mock", "pytz", "pep8", "nose"],
    test_suite='bisect_b2g.tests',
    author="John Ford",
    author_email="john@johnford.org",
    description="This program is used to bisect multiple repositories",
    license="MPL2",
    keywords="b2g gaia bisect",
    url="http://github.com/mozilla-b2g/b2g_bisect",
)
