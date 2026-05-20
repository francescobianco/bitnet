FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CONDA_DIR=/opt/conda
ENV PATH=$CONDA_DIR/bin:/root/.local/bin:$PATH
ENV CC=clang
ENV CXX=clang++

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    curl \
    wget \
    ca-certificates \
    gnupg \
    lsb-release \
    software-properties-common \
    python3.9 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://hf.co/cli/install.sh | bash

RUN wget -O /tmp/llvm.sh https://apt.llvm.org/llvm.sh && \
    chmod +x /tmp/llvm.sh && \
    /tmp/llvm.sh 18 all && \
    rm /tmp/llvm.sh

RUN update-alternatives --install /usr/bin/clang clang /usr/bin/clang-18 100 && \
    update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-18 100 && \
    update-alternatives --install /usr/bin/llvm-ar llvm-ar /usr/bin/llvm-ar-18 100 && \
    update-alternatives --install /usr/bin/llvm-ranlib llvm-ranlib /usr/bin/llvm-ranlib-18 100

RUN clang --version && clang++ --version && cmake --version

RUN curl -fsSL -o /tmp/miniforge.sh \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" \
    && bash /tmp/miniforge.sh -b -p $CONDA_DIR \
    && rm /tmp/miniforge.sh \
    && conda clean -afy

RUN git clone --recursive https://github.com/microsoft/BitNet.git /app/BitNet

WORKDIR /app/BitNet

RUN sed -i 's/int8_t \* y_col = y + col \* by;/const int8_t * y_col = y + col * by;/' src/ggml-bitnet-mad.cpp

RUN conda create -n bitnet-cpp python=3.9 -y

RUN conda run -n bitnet-cpp pip install --upgrade pip

RUN conda run -n bitnet-cpp pip install -r requirements.txt

RUN curl -LsSf https://hf.co/cli/install.sh | bash

RUN ls /root/.local/bin && /root/.local/bin/hf download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir models/BitNet-b1.58-2B-4T

RUN conda run -n bitnet-cpp python setup_env.py -md models/BitNet-b1.58-2B-4T -q i2_s || (cat logs/compile.log && false)

CMD ["conda", "run", "--no-capture-output", "-n", "bitnet-cpp", "python", "run_inference.py", "-m", "models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf", "-p", "Say hello from BitNet in one short sentence."]
