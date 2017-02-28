"""
This file is was taken idea from 'undocker' project. Thank for your work and
for shared the code. It was very useful to write this lib.

Undocker project:

https://github.com/larsks/undocker
"""

import os
import io
import errno
import shutil
import os.path
import tarfile
import logging
import hashlib
import tempfile

try:
    import ujson as json
except ImportError:
    import json

from typing import Dict, Tuple, List
from contextlib import closing, contextmanager
from dockerscan import DockerscanNotExitsError, DockerscanError

log = logging.getLogger("dockerscan")


# --------------------------------------------------------------------------
# Aux functions
# --------------------------------------------------------------------------
def _find_metadata_in_layers(img, id) -> dict:
    with closing(img.extractfile('%s/json' % id)) as fd:
        f_content = fd.read()
        if hasattr(f_content, "decode"):
            f_content = f_content.decode()
        yield json.loads(f_content)


def _find_layers(img, id):
    with closing(img.extractfile('%s/json' % id)) as fd:
        f_content = fd.read()
        if hasattr(f_content, "decode"):
            f_content = f_content.decode()
        info = json.loads(f_content)

    log.debug('layer = %s', id)
    for k in ['os', 'architecture', 'author', 'created']:
        if k in info:
            log.debug('%s = %s', k, info[k])

    yield id

    if 'parent' in info:
        pid = info['parent']
        for layer in _find_layers(img, pid):
            yield layer


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
@contextmanager
def open_docker_image(image_path: str,
                      image_repository: str = ""):
    """
    This function is a context manager that allow to open a docker image and
    return their layers and the layers metadata.

    yields img:TarFile, top_layers, image_and_tag, manifest

    >>> with open_docker_image("~/images/nginx:latest") as (img, first_layer, image_and_tag, manifest):
            print(img)
            print(first_layer)
            print(image_and_tag)
            print(manifest)
    <tarfile.TarFile object at 0x10464be48>
    '2dc9f5ef4d45394b3bfedbe23950de81cabd941519f59e163d243a7d4f859622'
    {'nginx': 'latest'}
    [{'Layers': ['8327c7df0d8cfe8652fc4be305e15e516b1b5bb48e13bb39780a87a58316c522/layer.tar', '076538d7e850181c3cccbdbce3a0811698efad376e2c99a72a203493739c2bf2/layer.tar', '2dc9f5ef4d45394b3bfedbe23950de81cabd941519f59e163d243a7d4f859622/layer.tar'], 'RepoTags': ['nginx:latest'], 'Config': 'db079554b4d2f7c65c4df3adae88cb72d051c8c3b8613eb44e86f60c945b1ca7.json'}]

    """
    tmp_image = os.path.basename(image_path)

    if ":" in tmp_image:
        image, tag = tmp_image.split(":", maxsplit=1)
    else:
        image, tag = tmp_image, "latest"

    #: Docker image layers and tags
    image_layers_tags = {}

    with tarfile.open(image_path, "r") as img:
        manifest_file = img.extractfile('manifest.json')
        manifest_content = manifest_file.read()
        if hasattr(manifest_content, "decode"):
            manifest_content = manifest_content.decode()
        manifest_content = json.loads(manifest_content)

        repos = img.extractfile('repositories')

        repo_content = repos.read()
        # If data are bytes, transform to str. JSON only accept str.
        if hasattr(repo_content, "decode"):
            repo_content = repo_content.decode()

        # Clean repo content
        repo_content = repo_content.replace("\n", "").replace("\r", "")

        repos_info = json.loads(repo_content)

        for name, tags in repos_info.items():
            image_layers_tags[name] = " ".join(tags)

        try:
            top_layers = repos_info[image][tag]
        except KeyError:
            try:
                image_and_repo = "{}/{}".format(image_repository,
                                                image)

                top_layers = repos_info[image_and_repo][tag]
            except KeyError:
                raise Exception(
                    'failed to find image {image} with tag {tag}'
                    ' (Command: "docker pull {image}:{tag}" will report '
                    'error). Try indicating the respository (option "-r") and'
                    'try again.'.format(image=image,
                                        tag=tag))

        yield img, top_layers, image_layers_tags, manifest_content


@contextmanager
def extract_layer_in_tmp_dir(img: tarfile.TarFile,
                             layer_digest: str) -> str:
    """
    This context manager allow to extract a selected layer into a temporal
    directory and yield the directory path

    >>> with open_docker_image(image_path,
                               image_repository) as (img,
                                                     top_layer,
                                                     _,
                                                     manifest):
            last_layer_digest = get_last_image_layer(manifest)
            with extract_layer_in_tmp_dir(img, last_layer_digest) as d:
                print(d)
    """
    with tempfile.TemporaryDirectory() as d:
        log.debug(" > Extracting layer content in temporal "
                  "dir: {}".format(d))

        extract_docker_layer(img, layer_digest, d)

        yield d


def get_last_image_layer(manifest: Dict) -> str:
    log.debug(" > Getting de last layer in the docker image")

    # Layers are ordered in inverse order
    return get_layers_ids_from_manifest(manifest)[-1]


def build_image_layer_from_dir(layer_name: str,
                               source_dir: str) -> Tuple[str, str]:
    """
    Create a new .tar docker layer from a directory content and return
    the new layer location and their digest

    >>> build_image_layer_from_dir("new_layer", "/tmp/new_layer/")
    "/tmp/new_layer/new_layer.tar", "076538d7e850181c3cccbdbce3a0811698efad376e2c99a72a203493739c2bf2"
    """
    if "tar" not in layer_name:
        layer_name = "{}.tar".format(layer_name)

    # Build new layer
    log.info(" > Building new {} layer image".format(layer_name))

    new_layer_path = os.path.join(source_dir, layer_name)
    with tarfile.open(new_layer_path, "w") as nl:
        nl.add(source_dir, arcname="/")

    # Calculating the digest
    log.info(" > Calculating new SHA256 hash for the new layer")

    with open(new_layer_path, "rb") as f:
        m = hashlib.sha256()
        m.update(f.read())
        new_layer_sha256 = m.hexdigest()

    return new_layer_path, new_layer_sha256


def build_manifest_with_new_layer(old_manifest: dict,
                                  old_layer_digest: str,
                                  new_layer_digest: str) -> dict:
    """
    Build a new manifest with the information of new layer and return the new
    manifest object

    :return: JSON with the new manifest
    """
    log.info(" > Updating the manifest")

    new_manifest = old_manifest.copy()

    for i, layer_id in enumerate(old_manifest[0]["Layers"]):
        if old_layer_digest in layer_id:
            new_manifest[0]["Layers"][i] = "{}/layer.tar" \
                                           "".format(new_layer_digest)
            break

    return new_manifest


def create_new_docker_image(manifest: dict,
                            image_output_path: str,
                            img: tarfile.TarFile,
                            old_layer_digest: str,
                            new_layer_path: str,
                            new_layer_digest: str):
    with tarfile.open(image_output_path, "w") as s:

        for f in img.getmembers():
            log.debug("    _> Processing file: {}".format(f.name))

            # Add new manifest
            if f.name == "manifest.json":
                # Dump Manifest to JSON
                new_manifest_json = json.dumps(manifest).encode()
                t = tarfile.TarInfo("manifest.json")
                t.size = len(new_manifest_json)

                s.addfile(t, io.BytesIO(new_manifest_json))

            # Add new trojanized layer
            elif old_layer_digest in f.name:
                # Skip for old layer.tar file
                if "layer" in f.name:
                    continue

                # Add the layer.tar
                if f.name == old_layer_digest:
                    log.debug(
                        "    _> Replacing layer {} by {}".format(
                            f.name,
                            new_layer_digest
                        ))
                    s.add(new_layer_path,
                          "{}/layer.tar".format(new_layer_digest))
                else:
                    #
                    # Extra files: "json" and "VERSION"
                    #
                    c = img.extractfile(f).read()
                    t = tarfile.TarInfo("{}/{}".format(
                        new_layer_digest,
                        os.path.basename(f.name)
                    ))

                    if "json" in f.name:
                        # Modify the JSON content to add the new
                        # hash
                        c = c.decode(). \
                            replace(old_layer_digest,
                                    new_layer_digest).encode()

                    t.size = len(c)
                    s.addfile(t, io.BytesIO(c))

            elif ".json" in f.name and "/" not in f.name:
                c = img.extractfile(f).read()
                t = tarfile.TarInfo(f.name)

                # Modify the JSON content to add the new
                # hash
                j = json.loads(c.decode())
                j["rootfs"]["diff_ids"][-1] = \
                    "sha256:{}".format(new_layer_digest)

                new_c = json.dumps(j).encode()

                t.size = len(new_c)
                s.addfile(t, io.BytesIO(new_c))

            # Add the rest of files / dirs
            else:
                s.addfile(f, img.extractfile(f))


def get_file_path_from_img(image_content_dir: str,
                           image_file_path: str) -> str:

    if image_file_path.startswith("/"):
        image_file_path = image_file_path[1:]

    return os.path.join(image_content_dir, image_file_path)


def copy_file_to_image_folder(image_content_dir: str,
                              src_file: str,
                              dst_file: str) -> str:

    if dst_file.startswith("/"):
        dst_file = dst_file[1:]

    remote_path = os.path.join(image_content_dir, dst_file)

    if not os.path.exists(remote_path):
        os.makedirs(remote_path)

    shutil.copy(src_file,
                remote_path)


def get_layers_ids_from_manifest(manifest: dict) -> List[str]:
    try:
        return [x.split("/")[0] for x in manifest[0]["Layers"]]

    except (IndexError, KeyError):
        raise DockerscanError("Invalid manifest")


def extract_docker_layer(img: tarfile.TarFile,
                         layer_id: str,
                         extract_path: str):
    with tarfile.open(fileobj=img.extractfile('%s/layer.tar' % layer_id),
                      errorlevel=0,
                      dereference=True) as layer:

        layer.extractall(path=extract_path)

        log.debug('processing whiteouts')
        for member in layer.getmembers():
            path = member.path
            if path.startswith('.wh.') or '/.wh.' in path:
                if path.startswith('.wh.'):
                    newpath = path[4:]
                else:
                    newpath = path.replace('/.wh.', '/')

                try:
                    log.debug('removing path %s', newpath)
                    os.unlink(path)
                    os.unlink(newpath)
                except OSError as err:
                    if err.errno != errno.ENOENT:
                        raise


def extract_docker_image(image_path: str,
                         extract_path: str,
                         image_repository: str):
    """Extract a docker image content to a path location"""
    if not os.path.exists(image_path):
        raise DockerscanNotExitsError("Docker image not exits at path: {}". \
                                      format(image_path))

    with open_docker_image(image_path,
                           image_repository) as (img, first_layer, _, _):
        layers = list(_find_layers(img, first_layer))

        if not os.path.isdir(extract_path):
            os.makedirs(extract_path)

        for layer_id in reversed(layers):
            log.debug('extracting layer %s', layer_id)

            extract_docker_layer(img, layer_id, extract_path)


def get_docker_image_layers(image_path: str,
                            image_repository: str) -> dict:
    """
    This function get a docker image layers and yield them

    >>> for x in get_docker_image_layers("/path/image.tar", "repo"):
            print(x)
    """
    with open_docker_image(image_path,
                           image_repository) as (img, top_layers, _, _):
        layers_meta = _find_metadata_in_layers(img, top_layers)

        for layer in layers_meta:
            yield layer


__all__ = ("open_docker_image", "extract_layer_in_tmp_dir",
           "get_last_image_layer", "get_docker_image_layers",
           "build_image_layer_from_dir", "build_manifest_with_new_layer",
           "get_file_path_from_img", "copy_file_to_image_folder",
           "extract_docker_image", "extract_docker_layer",
           "create_new_docker_image",
           "extract_docker_layer", "get_layers_ids_from_manifest")