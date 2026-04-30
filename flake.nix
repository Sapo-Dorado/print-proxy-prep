{
  description = "print-proxy-prep – prepare card images for printing";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachSystem [
      "x86_64-linux"
      "aarch64-linux"
      "x86_64-darwin"
      "aarch64-darwin"
    ] (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python3;

        gdown = python.pkgs.buildPythonPackage rec {
          pname = "gdown";
          version = "5.2.0";
          pyproject = true;

          src = python.pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-IUUWUGLYVSCjzZizVsntUixeeYTUCFNUCf1G+U3vx4c=";
          };

          build-system = with python.pkgs; [
            hatchling
            hatch-vcs
            hatch-fancy-pypi-readme
          ];

          dependencies = with python.pkgs; [
            requests
            tqdm
            beautifulsoup4
            filelock
          ];

          doCheck = false;
        };

        pythonEnv = python.withPackages (ps: [
          ps.pillow
          ps.reportlab
          ps.requests
          gdown
        ]);
      in
      {
        packages.default = pkgs.writeShellScriptBin "print-proxy-prep" ''
          exec ${pythonEnv}/bin/python ${self}/main.py "$@"
        '';

        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/print-proxy-prep";
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv ];
        };
      }
    );
}
