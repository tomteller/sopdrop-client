"""
Sopdrop - Houdini Asset Registry Client

Install: pip install sopdrop
For development: pip install -e .
"""

from setuptools import setup, find_packages
import os

# Read version from __init__.py
def get_version():
    init_path = os.path.join(os.path.dirname(__file__), "sopdrop", "__init__.py")
    with open(init_path) as f:
        for line in f:
            if line.startswith("__version__"):
                return line.split("=")[1].strip().strip('"').strip("'")
    return "0.1.0"

# Read long description from README
def get_long_description():
    readme_path = os.path.join(os.path.dirname(__file__), "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, encoding="utf-8") as f:
            return f.read()
    return ""

setup(
    name="sopdrop",
    version=get_version(),
    description="Houdini asset registry client - save, share, and install procedural nodes",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Sopdrop",
    author_email="hello@sopdrop.com",
    url="https://sopdrop.com",
    project_urls={
        "Documentation": "https://sopdrop.com/docs",
        "Source": "https://github.com/sopdrop/sopdrop",
        "Bug Tracker": "https://github.com/sopdrop/sopdrop/issues",
    },
    python_requires=">=3.7",
    packages=find_packages(),
    install_requires=[
        # No external deps - uses only stdlib for Houdini compatibility
        # Houdini ships with its own Python, and we want to avoid conflicts
    ],
    extras_require={
        "dev": [
            "pytest",
            "black",
            "mypy",
        ],
    },
    entry_points={
        "console_scripts": [
            "sopdrop=sopdrop.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Multimedia :: Graphics :: 3D Modeling",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
    ],
    keywords="houdini, sidefx, vfx, procedural, nodes, assets, package-manager",
)
