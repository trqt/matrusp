{
  description = "Python development environment with NumPy";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            python3
            python3Packages.aiohttp
            python3Packages.requests
            python3Packages.html5lib
            python3Packages.beautifulsoup4
            python3Packages.aiodns
            python3Packages.multi-key-dict
            python3Packages.python-dateutil

          ];

          shellHook = ''
            echo "Python development environment with NumPy activated"
            echo "Python version: $(python3 --version)"
          '';
        };
      }
    );
}
