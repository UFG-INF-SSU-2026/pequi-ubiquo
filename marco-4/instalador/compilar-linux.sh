#!/usr/bin/env bash
set -euo pipefail

source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 || $(dpkg --print-architecture) != amd64 ]]; then
    echo 'Compile em Ubuntu 24.04 amd64.' >&2
    exit 1
fi

installer=$(cd -- "$(dirname -- "$0")" && pwd)
server="$installer/../servidor"
output="$installer/saida"
version=4.1.1-1
name=towerai-servidor
artifact="${name}_${version}_amd64.deb"
build=$(mktemp -d)
trap 'rm -rf -- "$build"' EXIT

python3 -m venv "$build/venv"
python="$build/venv/bin/python"
"$python" -m pip install -r "$installer/requirements-build.txt"
"$python" -m PyInstaller --noconfirm --onedir --name "$name" \
    --distpath "$build/dist" --workpath "$build/pyinstaller" --specpath "$build" \
    --paths "$server" --collect-data faster_whisper --copy-metadata faster-whisper --copy-metadata huggingface_hub \
    --hidden-import huggingface_hub._snapshot_download --hidden-import huggingface_hub.hf_api \
    --hidden-import huggingface_hub.file_download "$server/desktop_app.py"

package="$build/package"
app="$package/usr/lib/$name"
docs="$package/usr/share/doc/$name"
install -d "$package/DEBIAN" "$package/usr/bin" "$package/usr/share/applications" "$app" "$docs/licencas"
cp -a "$build/dist/$name/." "$app/"
ln -s "../lib/$name/$name" "$package/usr/bin/$name"
install -m 644 "$installer/../docs/servidor-linux.md" "$docs/"
install -m 644 "$installer"/licencas/*.txt "$docs/licencas/"
"$python" "$installer/licencas_python.py" "$docs/licencas"
install -m 644 /usr/share/doc/python3.12/copyright "$docs/licencas/Python-LICENSE.txt"
for component in tcl8.6 tk8.6; do
    install -m 644 "/usr/share/doc/lib$component/copyright" "$docs/licencas/$component-LICENSE.txt"
done

llama_tag=b12590
llama_archive="$build/llama.tar.gz"
curl --fail --location --retry 2 --silent --show-error \
    "https://github.com/ggml-org/llama.cpp/releases/download/$llama_tag/llama-$llama_tag-bin-ubuntu-x64.tar.gz" \
    --output "$llama_archive"
printf '%s  %s\n' '1d3496a307edd845e206e7265c132f4229a9fdcd2113da677819b18bebf5abbd' "$llama_archive" | sha256sum --check
mkdir "$build/llama" "$app/llama"
tar -xzf "$llama_archive" --strip-components=1 -C "$build/llama"
cp -a "$build/llama/llama-server" "$build/llama/"libggml*.so* "$build/llama/"libllama.so* \
    "$build/llama/"libllama-common.so* "$build/llama/"libllama-server-impl.so* "$build/llama/"libmtmd.so* "$app/llama/"
if [[ -f $build/llama/LICENSE-LLVM-OpenMP ]]; then
    install -m 644 "$build/llama/LICENSE-LLVM-OpenMP" "$docs/licencas/"
fi
"$app/llama/llama-server" --version

person_model=object_detection_yolox_2022nov.onnx
install -d "$app/assets"
if [[ -f $server/modelos/$person_model ]]; then
    install -m 644 "$server/modelos/$person_model" "$app/assets/"
else
    curl --fail --location --retry 2 --silent --show-error \
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_detection_yolox/$person_model" \
        --output "$app/assets/$person_model"
fi
printf '%s  %s\n' 'c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063' \
    "$app/assets/$person_model" | sha256sum --check

cat > "$package/DEBIAN/control" <<EOF
Package: $name
Version: $version
Architecture: amd64
Maintainer: TowerAI <towerai@localhost>
Section: utils
Priority: optional
Installed-Size: $(du -sk "$package/usr" | cut -f1)
Depends: libc6 (>= 2.39), libgcc-s1, libstdc++6, libgomp1, libx11-6, libxext6, libxft2, libxrender1, libxss1, fonts-dejavu-core, ca-certificates, iproute2, xdg-utils
Description: Servidor local TowerAI para reunioes e transcricao
 Painel para configurar o microfone Android e a webcam, transcrever o audio
 da reuniao com Whisper e gerar a ata localmente com llama.cpp.
EOF

install -m 644 "$installer/towerai-servidor.desktop" "$package/usr/share/applications/"
dpkg-deb --root-owner-group -Zxz -z3 --build "$package" "$build/$artifact"
mkdir -p "$output"
install -m 644 "$build/$artifact" "$output/"
printf 'Pacote criado em %s/%s\n' "$output" "$artifact"
